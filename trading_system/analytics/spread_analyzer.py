"""
analytics/spread_analyzer.py – Bid/ask spread quality & liquidity scoring.

The spread is the MOST IMPORTANT execution cost in options scalping.
This module answers the three key questions every options scalper must ask:

  1. Is the spread too wide to trade profitably?
  2. How liquid is this contract?
  3. Will my order fill near mid-price, or will I get slippage?

Scoring (0–100, higher = better):
  ──────────────────────────────────────────────────────────
  Spread Score   = 100 × (1 - clamp(spread_pct / max_spread, 0, 1))
  Liquidity Score = composite of volume, OI, and spread_abs
  Fill Quality    = estimate of how close to mid you'll fill
  ──────────────────────────────────────────────────────────
"""

import logging
import math

import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

logger = logging.getLogger(__name__)

# Liquidity scoring thresholds
_VOL_EXCELLENT = 1_000
_VOL_GOOD = 300
_VOL_FAIR = 50
_OI_EXCELLENT = 5_000
_OI_GOOD = 1_000
_OI_FAIR = 200


def spread_score(bid: float, ask: float) -> float:
    """
    Score the bid-ask spread quality (0 = untradeable, 100 = perfect).

    Penalises both:
      • Absolute spread  (> $0.50 → poor for cheap options)
      • Relative spread  (> 10% of mid → expensive)
    """
    if ask <= 0 or bid < 0:
        return 0.0
    mid = (bid + ask) / 2
    if mid <= 0:
        return 0.0

    spread_abs = ask - bid
    spread_pct = spread_abs / mid  # decimal

    # Absolute penalty
    abs_penalty = min(spread_abs / config.MAX_SPREAD_ABS, 1.0)
    # Relative penalty
    pct_penalty = min(spread_pct / config.MAX_SPREAD_PCT, 1.0)

    # Weighted combination – relative is more important for scalping
    combined_penalty = 0.4 * abs_penalty + 0.6 * pct_penalty
    score = max(0.0, 100.0 * (1 - combined_penalty))
    return round(score, 1)


def liquidity_score(volume: int, open_interest: int, spread_abs: float) -> float:
    """
    Score the liquidity of a contract (0–100).

    Uses volume and open interest as the primary signals, with a small
    spread penalty for very illiquid contracts.
    """
    # Volume sub-score (log scale)
    if volume >= _VOL_EXCELLENT:
        vol_sub = 100.0
    elif volume >= _VOL_GOOD:
        vol_sub = 70.0 + 30.0 * (volume - _VOL_GOOD) / (_VOL_EXCELLENT - _VOL_GOOD)
    elif volume >= _VOL_FAIR:
        vol_sub = 40.0 + 30.0 * (volume - _VOL_FAIR) / (_VOL_GOOD - _VOL_FAIR)
    else:
        vol_sub = 40.0 * volume / max(_VOL_FAIR, 1)

    # OI sub-score (log scale)
    if open_interest >= _OI_EXCELLENT:
        oi_sub = 100.0
    elif open_interest >= _OI_GOOD:
        oi_sub = 70.0 + 30.0 * (open_interest - _OI_GOOD) / (_OI_EXCELLENT - _OI_GOOD)
    elif open_interest >= _OI_FAIR:
        oi_sub = 40.0 + 30.0 * (open_interest - _OI_FAIR) / (_OI_GOOD - _OI_FAIR)
    else:
        oi_sub = 40.0 * open_interest / max(_OI_FAIR, 1)

    # Spread penalty (reduce score if spread is large)
    spread_penalty = min(spread_abs / config.MAX_SPREAD_ABS, 1.0) * 20.0

    score = (0.5 * vol_sub + 0.5 * oi_sub) - spread_penalty
    return round(max(0.0, min(100.0, score)), 1)


def fill_quality_estimate(bid: float, ask: float) -> dict:
    """
    Estimate how close to mid-price an order is likely to fill.

    Returns:
        fill_price      : estimated fill (between bid and ask)
        slippage_pct    : estimated slippage as % of mid
        tradeable       : bool – True if spread is within policy limits
        verdict         : human-readable verdict string
    """
    if ask <= 0:
        return {
            "fill_price": 0.0,
            "slippage_pct": 999.0,
            "tradeable": False,
            "verdict": "NO BID/ASK – untradeable",
        }

    mid = (bid + ask) / 2
    spread = ask - bid
    spread_pct = spread / mid if mid > 0 else 999.0

    # Narrow spread → fill near mid (small slippage)
    # Wide spread   → fill closer to ask (more slippage)
    fill_ratio = min(spread_pct / config.MAX_SPREAD_PCT, 1.0)
    # fill_ratio = 0  → fill at mid
    # fill_ratio = 1  → fill at ask
    fill_price = mid + fill_ratio * (spread / 2)
    slippage_pct = ((fill_price - mid) / mid * 100) if mid > 0 else 999.0

    tradeable = (
        spread <= config.MAX_SPREAD_ABS
        and spread_pct <= config.MAX_SPREAD_PCT
    )

    if spread_pct < 0.03:
        verdict = "EXCELLENT – fill at/near mid"
    elif spread_pct < config.MAX_SPREAD_PCT:
        verdict = "ACCEPTABLE – slight slippage expected"
    elif spread_pct < config.MAX_SPREAD_PCT * 2:
        verdict = "WIDE – significant slippage; size down"
    else:
        verdict = "TOO WIDE – DO NOT TRADE this contract"

    return {
        "fill_price": round(fill_price, 3),
        "slippage_pct": round(slippage_pct, 2),
        "tradeable": tradeable,
        "verdict": verdict,
    }


def analyse_chain_execution(chain_df: pd.DataFrame) -> pd.DataFrame:
    """
    Vectorised execution analysis for an entire options-chain DataFrame.

    Adds columns:
        spread_score, liquidity_score, fill_estimate, tradeable, execution_verdict
    """
    if chain_df.empty:
        return chain_df

    df = chain_df.copy()

    ss, ls, fill_est, tradeable, verdict = [], [], [], [], []
    for _, row in df.iterrows():
        bid = float(row.get("bid", 0))
        ask = float(row.get("ask", 0))
        vol = int(row.get("volume", 0))
        oi = int(row.get("openInterest", 0))
        spread_abs = ask - bid

        s = spread_score(bid, ask)
        l = liquidity_score(vol, oi, spread_abs)
        fq = fill_quality_estimate(bid, ask)

        ss.append(s)
        ls.append(l)
        fill_est.append(fq["fill_price"])
        tradeable.append(fq["tradeable"])
        verdict.append(fq["verdict"])

    df["spread_score"] = ss
    df["liquidity_score"] = ls
    df["fill_estimate"] = fill_est
    df["tradeable"] = tradeable
    df["execution_verdict"] = verdict
    return df
