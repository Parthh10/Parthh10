"""
data/yfinance_fetcher.py – Yahoo Finance data layer (100 % free, no API key).

Provides:
  • get_underlying_quote(ticker)       → latest price, volume, market cap
  • get_options_chain(ticker, expiry)  → calls + puts with bid/ask/IV/volume/OI
  • get_all_expirations(ticker)        → list of available expiry date strings
  • get_historical(ticker, period)     → OHLCV DataFrame
  • get_options_full(ticker)           → all expirations merged, with DTE column

Note:
  Yahoo Finance data has a ~15-minute delay for options and is subject to
  rate-limiting. Use Tradier (tradier_fetcher.py) when a free API token is
  available for real-time accuracy.
"""

import logging
from datetime import datetime, date
from typing import Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


def get_underlying_quote(ticker: str) -> dict:
    """Return the current underlying price and key stats."""
    t = yf.Ticker(ticker)
    info = t.fast_info
    try:
        price = info.last_price
        prev_close = info.previous_close
        day_volume = info.three_month_average_volume  # proxy when live vol unavailable
        market_cap = getattr(info, "market_cap", None)
    except Exception as exc:
        logger.warning("fast_info failed for %s: %s", ticker, exc)
        price, prev_close, day_volume, market_cap = None, None, None, None

    return {
        "ticker": ticker,
        "price": price,
        "prev_close": prev_close,
        "day_volume": day_volume,
        "market_cap": market_cap,
        "timestamp": datetime.utcnow().isoformat(),
    }


def get_all_expirations(ticker: str) -> list[str]:
    """Return all available expiration date strings for a ticker."""
    t = yf.Ticker(ticker)
    expirations = list(t.options)
    logger.debug("%s: %d expiration dates available", ticker, len(expirations))
    return expirations


def _dte(expiry_str: str) -> int:
    """Days to expiry from today."""
    expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    return max(0, (expiry_date - date.today()).days)


def get_options_chain(ticker: str, expiry: str) -> pd.DataFrame:
    """
    Fetch calls + puts for a single expiry.

    Returns a combined DataFrame with columns:
        ticker, expiry, dte, option_type, contractSymbol,
        strike, bid, ask, mid, spread, spread_pct,
        lastPrice, volume, openInterest, impliedVolatility
    """
    t = yf.Ticker(ticker)
    try:
        chain = t.option_chain(expiry)
    except Exception as exc:
        logger.error("Failed to fetch chain %s %s: %s", ticker, expiry, exc)
        return pd.DataFrame()

    dte = _dte(expiry)
    frames = []
    for option_type, df in [("call", chain.calls), ("put", chain.puts)]:
        df = df.copy()
        df["option_type"] = option_type
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True)

    # ── Derived columns ────────────────────────────────────────────────────
    combined["ticker"] = ticker
    combined["expiry"] = expiry
    combined["dte"] = dte
    combined["bid"] = pd.to_numeric(combined.get("bid", 0), errors="coerce").fillna(0)
    combined["ask"] = pd.to_numeric(combined.get("ask", 0), errors="coerce").fillna(0)
    combined["mid"] = (combined["bid"] + combined["ask"]) / 2
    combined["spread"] = combined["ask"] - combined["bid"]
    combined["spread_pct"] = combined.apply(
        lambda r: (r["spread"] / r["mid"] * 100) if r["mid"] > 0 else 999.0, axis=1
    )
    combined["volume"] = pd.to_numeric(
        combined.get("volume", 0), errors="coerce"
    ).fillna(0).astype(int)
    combined["openInterest"] = pd.to_numeric(
        combined.get("openInterest", 0), errors="coerce"
    ).fillna(0).astype(int)
    combined["impliedVolatility"] = pd.to_numeric(
        combined.get("impliedVolatility", None), errors="coerce"
    )

    logger.debug(
        "%s %s (%d DTE): %d contracts fetched", ticker, expiry, dte, len(combined)
    )
    return combined


def get_options_full(
    ticker: str,
    max_dte: int = 45,
    min_dte: int = 5,
) -> pd.DataFrame:
    """
    Fetch ALL expirations within the DTE window and return one merged DataFrame.
    """
    expirations = get_all_expirations(ticker)
    frames = []
    for exp in expirations:
        dte = _dte(exp)
        if min_dte <= dte <= max_dte:
            df = get_options_chain(ticker, exp)
            if not df.empty:
                frames.append(df)
    if not frames:
        logger.warning("%s: no options found in %d-%d DTE window", ticker, min_dte, max_dte)
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def get_historical(
    ticker: str,
    period: str = "1y",
    interval: str = "1d",
) -> pd.DataFrame:
    """Return OHLCV historical data (used for IV rank calculation)."""
    t = yf.Ticker(ticker)
    hist = t.history(period=period, interval=interval)
    hist.index = hist.index.tz_localize(None)
    return hist
