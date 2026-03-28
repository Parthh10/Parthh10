"""
engine/trade_filter.py – Final threshold-based trade filtering engine.

Applies the composite score plus any additional confirmations to produce
a ranked, tradeable watchlist.

Each ticker in the watchlist gets:
  • A verdict         (STRONG BUY / BUY / WATCH / SKIP)
  • Suggested sizing  (% of max position, conservative by default)
  • Best contract     (strike, expiry, mid-price)
  • Risk annotations  (spread warnings, IV cautions, etc.)

Usage:
    watchlist = build_watchlist(scored_results)
    print_watchlist(watchlist)
"""

import logging
from typing import Optional

import pandas as pd
from tabulate import tabulate

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

logger = logging.getLogger(__name__)


def _position_size(composite_score: float, exec_approved: bool) -> str:
    """
    Suggest a position size as a % of max allocation.
    Conservative sizing: scale into positions, never full-size on first signal.
    """
    if not exec_approved:
        return "0% – execution rejected"
    if composite_score >= 80:
        return "75–100% of max"
    elif composite_score >= 70:
        return "50–75% of max"
    elif composite_score >= config.MIN_COMPOSITE_SCORE:
        return "25–50% of max"
    elif composite_score >= 45:
        return "10–25% (watch only)"
    return "0% – skip"


def build_watchlist(scored_results: list[dict]) -> pd.DataFrame:
    """
    Convert a list of per-ticker score dicts into a sorted watchlist DataFrame.

    Each dict in scored_results should have the structure returned by
    scoring_engine.compute_composite_score() plus 'ticker' and 'best_contract'.

    Returns DataFrame sorted by composite_score descending.
    """
    rows = []
    for r in scored_results:
        ticker = r.get("ticker", "?")
        score = r.get("composite_score", 0.0)
        verdict = r.get("verdict", "SKIP")
        direction = r.get("dominant_direction", "unknown")
        exec_approved = r.get("exec_result", {}).get("approved", False)
        size = _position_size(score, exec_approved)

        # Best contract details
        bc = r.get("best_contract")
        if bc is not None:
            contract_symbol = bc.get("contractSymbol", "?")
            strike = bc.get("strike", "?")
            expiry = bc.get("expiry", "?")
            dte = bc.get("dte", "?")
            mid = bc.get("mid", "?")
            delta_val = bc.get("bs_delta") or bc.get("delta") or "?"
        else:
            contract_symbol = strike = expiry = dte = mid = delta_val = "?"

        # Risk flags
        risk_flags = []
        exec_result = r.get("exec_result", {})
        if exec_result.get("warnings"):
            risk_flags.extend(exec_result["warnings"])
        if r.get("iv_score", 100) < 35:
            risk_flags.append("IV elevated – expensive premium")
        if r.get("num_sweeps", 0) >= 3:
            risk_flags.append(f"{r['num_sweeps']} sweeps detected")

        rows.append({
            "ticker": ticker,
            "verdict": verdict,
            "score": score,
            "direction": direction,
            "contract": contract_symbol,
            "strike": strike,
            "expiry": expiry,
            "dte": dte,
            "mid": mid,
            "delta": delta_val if delta_val != "?" else "?",
            "flow_score": r.get("flow_score", 0),
            "exec_score": r.get("execution_score", 0),
            "greeks_score": r.get("greeks_score", 0),
            "iv_score": r.get("iv_score", 0),
            "size": size,
            "risk_flags": " | ".join(risk_flags) if risk_flags else "—",
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("score", ascending=False).reset_index(drop=True)


def filter_actionable(watchlist: pd.DataFrame) -> pd.DataFrame:
    """Return only rows with verdict BUY or STRONG BUY."""
    if watchlist.empty:
        return watchlist
    return watchlist[watchlist["verdict"].isin(["BUY", "STRONG BUY"])].reset_index(drop=True)


def print_watchlist(watchlist: pd.DataFrame, show_all: bool = False) -> None:
    """Pretty-print the watchlist to stdout using tabulate."""
    if watchlist.empty:
        print("\n  No qualifying trades found in this scan.\n")
        return

    display = watchlist if show_all else filter_actionable(watchlist)
    if display.empty:
        print(
            f"\n  Scan complete. Best composite score: {watchlist['score'].max():.1f}  "
            f"(threshold = {config.MIN_COMPOSITE_SCORE}). No trades meet criteria.\n"
        )
        return

    cols = ["ticker", "verdict", "score", "direction", "strike", "expiry", "dte",
            "mid", "size", "flow_score", "exec_score", "risk_flags"]
    available = [c for c in cols if c in display.columns]
    print("\n" + "═" * 90)
    print("  TRADING WATCHLIST")
    print("═" * 90)
    print(tabulate(display[available], headers="keys", tablefmt="rounded_outline",
                   floatfmt=".1f", showindex=False))
    print("═" * 90 + "\n")
