"""
main.py – Orchestrator for the free-tier options trading system.

Flow:
  For each ticker in the configured universe:
    1. Fetch options chain (Tradier if token set, else Yahoo Finance)
    2. Compute / enrich Greeks (Black-Scholes fallback)
    3. Analyse spread & execution quality
    4. Detect unusual options flow
    5. Compute IV stats (rank, percentile, regime)
    6. Run contract selector to find the best trade
    7. Score everything with the composite engine
    8. Fire alerts for sweeps and signals
    9. Print formatted watchlist

Run:
    pip install -r requirements.txt
    python main.py                         # uses Yahoo Finance (no key needed)
    TRADIER_TOKEN=xxx python main.py       # uses Tradier for real-time Greeks

Optional .env file (copy .env.example → .env):
    TRADIER_TOKEN=your_token_here
    TRADIER_SANDBOX=true
"""

import os
import sys
import logging
import traceback
from typing import Optional

import pandas as pd

# ── Ensure the trading_system/ folder is on sys.path ──────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from alerts.alert_system import (
    setup_logging,
    sweep_alert,
    signal_alert,
    watch_alert,
    caution_alert,
    info_alert,
)
from analytics.greeks import enrich_chain_with_greeks
from analytics.iv_calculator import compute_iv_stats, atm_iv
from analytics.spread_analyzer import analyse_chain_execution
from engine.contract_selector import select_best_contract
from engine.execution_filter import check_execution
from engine.flow_detector import detect_unusual_flow, ticker_flow_summary
from engine.scoring_engine import compute_composite_score, score_label
from engine.trade_filter import build_watchlist, print_watchlist, filter_actionable

logger = logging.getLogger("main")


# ─────────────────────────────────────────────────────────────────────────────
# Data layer: choose Tradier (real-time) or Yahoo Finance (free, no key)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_chain(ticker: str) -> tuple[pd.DataFrame, float]:
    """
    Fetch the full options chain and underlying price.

    Returns (chain_df, underlying_price).
    Tries Tradier first; falls back to Yahoo Finance.
    """
    from data import tradier_fetcher, yfinance_fetcher

    using_tradier = tradier_fetcher.is_available()

    if using_tradier:
        try:
            expirations = tradier_fetcher.get_expirations(ticker)
            if not expirations:
                raise ValueError("No expirations from Tradier")
            quote = tradier_fetcher.get_underlying_quote(ticker)
            underlying_price = quote.get("price", 0)

            frames = []
            for exp in expirations:
                from datetime import datetime, date
                dte_days = max(0, (datetime.strptime(exp, "%Y-%m-%d").date() - date.today()).days)
                if config.MIN_DTE <= dte_days <= config.MAX_DTE:
                    df_exp = tradier_fetcher.get_options_chain(ticker, exp)
                    if not df_exp.empty:
                        df_exp["dte"] = dte_days
                        frames.append(df_exp)

            if frames:
                chain = pd.concat(frames, ignore_index=True)
                logger.info("%s: %d contracts from Tradier", ticker, len(chain))
                return chain, underlying_price
        except Exception as exc:
            logger.warning("Tradier failed for %s (%s) – falling back to yfinance", ticker, exc)

    # Yahoo Finance fallback
    quote = yfinance_fetcher.get_underlying_quote(ticker)
    underlying_price = quote.get("price") or 0
    chain = yfinance_fetcher.get_options_full(
        ticker, max_dte=config.MAX_DTE, min_dte=config.MIN_DTE
    )
    logger.info("%s: %d contracts from Yahoo Finance", ticker, len(chain))
    return chain, float(underlying_price or 0)


# ─────────────────────────────────────────────────────────────────────────────
# Per-ticker pipeline
# ─────────────────────────────────────────────────────────────────────────────

def analyse_ticker(ticker: str) -> Optional[dict]:
    """
    Run the full analysis pipeline for one ticker.
    Returns a dict suitable for build_watchlist(), or None on error.
    """
    logger.info("── Analysing %s ──", ticker)

    try:
        # 1. Fetch chain
        chain, underlying_price = _fetch_chain(ticker)
        if chain.empty or underlying_price <= 0:
            logger.warning("%s: empty chain or no price – skipping", ticker)
            return None

        # 2. Enrich with computed Greeks (Black-Scholes)
        chain = enrich_chain_with_greeks(chain, underlying_price, config.RISK_FREE_RATE)

        # 3. Spread & execution analysis
        chain = analyse_chain_execution(chain)

        # 4. Unusual flow detection
        chain = detect_unusual_flow(chain)
        flow_summary = ticker_flow_summary(chain)

        # 5. IV stats
        from data import yfinance_fetcher
        history = yfinance_fetcher.get_historical(ticker, period="1y")
        current_iv = atm_iv(chain, underlying_price)
        iv_stats = compute_iv_stats(
            ticker,
            current_options_iv=current_iv,
            history=history,
        )

        # 6. Determine dominant direction from flow
        direction = flow_summary.get("dominant_direction", "neutral")
        if direction == "neutral":
            direction = "call"   # default to bullish scan; system is symmetric

        # 7. Contract selection
        best_contract = select_best_contract(chain, direction=direction)

        # 8. Execution check
        exec_result: dict
        if best_contract is not None:
            exec_result = check_execution(best_contract, iv_stats)
        else:
            exec_result = {"approved": False, "exec_score": 0, "reasons": ["No qualifying contract found"]}

        # 9. Composite score
        score_result = compute_composite_score(
            ticker_flow=flow_summary,
            exec_result=exec_result,
            best_contract=best_contract,
            iv_stats=iv_stats,
            direction=direction,
        )
        score_result["ticker"] = ticker
        score_result["exec_result"] = exec_result
        score_result["iv_stats"] = iv_stats
        score_result["best_contract"] = best_contract.to_dict() if best_contract is not None else None

        # 10. Fire sweep alerts
        sweeps = chain[chain["is_sweep"]] if "is_sweep" in chain.columns else pd.DataFrame()
        if not sweeps.empty:
            top_sweep = sweeps.nlargest(1, "total_premium").iloc[0]
            sweep_alert(
                ticker=ticker,
                contract_symbol=str(top_sweep.get("contractSymbol", "?")),
                direction=str(top_sweep.get("flow_direction", "?")),
                total_premium=float(top_sweep.get("total_premium", 0)),
                flow_score=float(top_sweep.get("flow_score", 0)),
            )

        # 11. Signal / watch alerts
        composite = score_result["composite_score"]
        verdict = score_result["verdict"]
        label = score_label(composite)

        if verdict in ("BUY", "STRONG BUY"):
            signal_alert(
                ticker=ticker,
                verdict=verdict,
                composite_score=composite,
                best_contract=score_result["best_contract"],
            )
        elif verdict == "WATCH":
            watch_alert(ticker, composite, "Approaching threshold")
        else:
            info_alert(ticker, f"{label} – {verdict}")

        # Log IV regime
        if iv_stats:
            logger.info(
                "%s IV: rank=%.0f  percentile=%.0f  regime=%s",
                ticker,
                iv_stats.get("iv_rank", 0),
                iv_stats.get("iv_percentile", 0),
                iv_stats.get("iv_regime", "?"),
            )

        return score_result

    except Exception as exc:
        logger.error("Error analysing %s: %s", ticker, exc)
        logger.debug(traceback.format_exc())
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main(tickers: Optional[list[str]] = None) -> None:
    setup_logging()

    tickers = tickers or config.DEFAULT_TICKERS
    using_tradier = bool(config.TRADIER_TOKEN)

    print("\n" + "═" * 70)
    print("  FREE-TIER OPTIONS TRADING SYSTEM")
    print("  Data source:", "Tradier (real-time)" if using_tradier else "Yahoo Finance (15-min delay)")
    print(f"  Scanning {len(tickers)} tickers: {', '.join(tickers)}")
    print("═" * 70 + "\n")

    scored_results = []
    for ticker in tickers:
        result = analyse_ticker(ticker)
        if result:
            scored_results.append(result)

    watchlist = build_watchlist(scored_results)
    print_watchlist(watchlist, show_all=False)

    actionable = filter_actionable(watchlist)
    if not actionable.empty:
        print(f"  {len(actionable)} actionable trade(s) found.\n")
    else:
        print("  No trades meet the composite score threshold today.\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Free-Tier Options Trading System")
    parser.add_argument(
        "--tickers", nargs="+", default=None,
        help="Override default ticker list (e.g. --tickers SPY AAPL TSLA)"
    )
    args = parser.parse_args()
    main(args.tickers)
