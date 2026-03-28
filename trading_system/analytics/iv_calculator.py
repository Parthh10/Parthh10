"""
analytics/iv_calculator.py – Implied Volatility surface, rank, and percentile.

Key outputs:
  • IV Rank (IVR)   – where today's IV stands in its 52-week range (0–100)
  • IV Percentile   – % of days in the past year where IV was BELOW today
  • IV Surface      – heatmap of IV across (strike × DTE) grid
  • IV Term Structure – IV vs. DTE for ATM options

IV Rank formula:
    IVR = (current_IV - 52w_low) / (52w_high - 52w_low) * 100

IV Percentile formula:
    IVP = count(daily_IV < current_IV) / total_days * 100

Both are computed from historical IV derived from Yahoo Finance closing prices
(using close-to-close historical volatility as a proxy, refined to 30-day HV).
"""

import logging
import math
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def historical_volatility(
    prices: pd.Series,
    window: int = 30,
    annualise: bool = True,
) -> pd.Series:
    """
    Close-to-close historical volatility (log returns std).

    Args:
        prices   : daily closing prices
        window   : rolling window in days
        annualise: multiply by sqrt(252) if True
    """
    log_ret = np.log(prices / prices.shift(1)).dropna()
    hv = log_ret.rolling(window).std()
    if annualise:
        hv = hv * math.sqrt(252)
    return hv


def iv_rank(current_iv: float, iv_series: pd.Series) -> float:
    """
    IVR = (current_IV - min) / (max - min) * 100

    Higher IVR → IV is historically elevated → selling premium is more attractive.
    Lower  IVR → IV is historically compressed → buying premium may be cheaper.
    """
    series = iv_series.dropna()
    if series.empty:
        return float("nan")
    lo = series.min()
    hi = series.max()
    if hi == lo:
        return 50.0
    return float(max(0, min(100, (current_iv - lo) / (hi - lo) * 100)))


def iv_percentile(current_iv: float, iv_series: pd.Series) -> float:
    """
    IVP = fraction of historical days where IV was below today's IV.
    """
    series = iv_series.dropna()
    if series.empty:
        return float("nan")
    below = (series < current_iv).sum()
    return float(below / len(series) * 100)


def compute_iv_stats(
    ticker: str,
    current_options_iv: Optional[float] = None,
    history: Optional[pd.DataFrame] = None,
    hv_window: int = 30,
) -> dict:
    """
    Compute IVR, IVP, and HV for a ticker.

    Args:
        ticker              : stock symbol (used to fetch history if not provided)
        current_options_iv  : ATM implied vol from options chain (decimal).
                              If None, uses HV30 as proxy.
        history             : pre-fetched OHLCV DataFrame (from yfinance_fetcher).
                              If None, fetches automatically.
        hv_window           : rolling window for HV calculation
    Returns dict:
        {
          "hv30"        : float  – 30-day historical vol (annualised)
          "current_iv"  : float  – ATM IV used (from options or HV proxy)
          "iv_rank"     : float  – 0-100
          "iv_percentile": float – 0-100
          "iv_regime"   : str   – "low" / "normal" / "elevated" / "extreme"
        }
    """
    if history is None:
        from data.yfinance_fetcher import get_historical
        history = get_historical(ticker, period="1y")

    closes = history["Close"].dropna()
    if closes.empty:
        logger.warning("No close data for %s", ticker)
        return {}

    hv_series = historical_volatility(closes, window=hv_window)
    hv_current = float(hv_series.iloc[-1]) if not hv_series.dropna().empty else float("nan")

    # Use options-chain IV when available, else fall back to HV30
    iv = current_options_iv if (current_options_iv and not math.isnan(current_options_iv)) else hv_current

    ivr = iv_rank(iv, hv_series)
    ivp = iv_percentile(iv, hv_series)

    # Regime classification
    if ivr < 20:
        regime = "low"
    elif ivr < 50:
        regime = "normal"
    elif ivr < 80:
        regime = "elevated"
    else:
        regime = "extreme"

    return {
        "hv30": round(hv_current * 100, 2),            # as percentage
        "current_iv": round(iv * 100, 2),              # as percentage
        "iv_rank": round(ivr, 1),
        "iv_percentile": round(ivp, 1),
        "iv_regime": regime,
    }


def build_iv_surface(chain_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build an IV surface matrix: rows = strike, columns = expiry DTE.

    Input  : combined options-chain DataFrame with columns
             [option_type, strike, dte, computed_iv / impliedVolatility]
    Output : pivot table (strike × dte) filled with ATM-closest IV values.
             Useful for plotting with matplotlib.
    """
    if chain_df.empty:
        return pd.DataFrame()

    iv_col = "computed_iv" if "computed_iv" in chain_df.columns else "impliedVolatility"
    df = chain_df[["option_type", "strike", "dte", iv_col]].copy()
    df = df.dropna(subset=[iv_col])
    df = df[df[iv_col] > 0]

    # Use calls for the surface (conventional)
    calls = df[df["option_type"] == "call"]
    if calls.empty:
        calls = df  # fallback to all

    surface = calls.pivot_table(
        index="strike",
        columns="dte",
        values=iv_col,
        aggfunc="mean",
    )
    return surface


def atm_iv(chain_df: pd.DataFrame, underlying_price: float) -> float:
    """
    Return the implied volatility of the at-the-money call option
    closest to the current underlying price.
    """
    if chain_df.empty:
        return float("nan")

    iv_col = "computed_iv" if "computed_iv" in chain_df.columns else "impliedVolatility"
    calls = chain_df[chain_df["option_type"] == "call"].copy()
    calls = calls.dropna(subset=[iv_col])
    if calls.empty:
        return float("nan")

    calls["strike_distance"] = abs(calls["strike"] - underlying_price)
    nearest = calls.nsmallest(1, "strike_distance")
    if nearest.empty:
        return float("nan")
    return float(nearest[iv_col].iloc[0])


def plot_iv_surface(surface: pd.DataFrame, ticker: str = "") -> None:
    """
    Quick matplotlib plot of the IV surface.
    Call this from a notebook or debug session to visualise IV.
    """
    try:
        import matplotlib.pyplot as plt
        from matplotlib import cm

        if surface.empty:
            print("IV surface is empty – nothing to plot.")
            return

        fig = plt.figure(figsize=(12, 7))
        ax = fig.add_subplot(111, projection="3d")
        strikes = surface.index.to_numpy(dtype=float)
        dtes = surface.columns.to_numpy(dtype=float)
        Z = surface.values * 100   # convert to %

        X, Y = np.meshgrid(dtes, strikes)
        ax.plot_surface(X, Y, Z, cmap=cm.RdYlGn_r, edgecolor="none", alpha=0.85)
        ax.set_xlabel("DTE")
        ax.set_ylabel("Strike")
        ax.set_zlabel("IV (%)")
        ax.set_title(f"IV Surface – {ticker}" if ticker else "IV Surface")
        plt.tight_layout()
        plt.show()
    except Exception as exc:
        logger.warning("Could not plot IV surface: %s", exc)
