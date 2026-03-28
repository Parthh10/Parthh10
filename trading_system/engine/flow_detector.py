"""
engine/flow_detector.py – Unusual options flow detection.

Mimics the core signal that Unusual Whales / Cheddar Flow produces,
using only FREE data from Yahoo Finance.

Detection logic:
  1. Volume / Open-Interest ratio  (> threshold = unusual activity today)
  2. Premium swept                 (volume × mid-price > $X = large bet)
  3. Direction bias                (call-heavy vs put-heavy flow)
  4. Expiry urgency                (short-DTE sweeps = aggressive)
  5. Sweep flag                    (very high vol/OI with significant premium)

Each contract gets a Flow Score (0–100).
The ticker-level flow score is the max single-contract score plus a
contribution from the number of unusual contracts found.
"""

import logging
import math
from typing import Optional

import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Contract-level flow scoring
# ─────────────────────────────────────────────────────────────────────────────

def _vol_oi_score(volume: int, open_interest: int) -> float:
    """Score based on volume/OI ratio (0–40 points)."""
    if open_interest < 1:
        return 0.0
    ratio = volume / open_interest
    if ratio >= 5 * config.UNUSUAL_FLOW_VOL_OI_RATIO:
        return 40.0
    elif ratio >= 2 * config.UNUSUAL_FLOW_VOL_OI_RATIO:
        return 30.0
    elif ratio >= config.UNUSUAL_FLOW_VOL_OI_RATIO:
        return 20.0
    elif ratio >= 1.0:
        return 10.0
    return 0.0


def _premium_score(volume: int, mid_price: float) -> float:
    """Score based on total premium swept (0–30 points)."""
    total_premium = volume * mid_price * 100   # 1 contract = 100 shares
    if total_premium >= 10 * config.UNUSUAL_FLOW_MIN_PREMIUM:
        return 30.0
    elif total_premium >= 5 * config.UNUSUAL_FLOW_MIN_PREMIUM:
        return 25.0
    elif total_premium >= config.UNUSUAL_FLOW_MIN_PREMIUM:
        return 20.0
    elif total_premium >= config.UNUSUAL_FLOW_MIN_PREMIUM / 5:
        return 10.0
    return 0.0


def _expiry_urgency_score(dte: int) -> float:
    """Short-DTE sweeps are more aggressive / urgent (0–20 points)."""
    if dte <= 3:
        return 20.0
    elif dte <= 7:
        return 18.0
    elif dte <= 14:
        return 14.0
    elif dte <= 21:
        return 10.0
    elif dte <= 45:
        return 6.0
    return 2.0


def _otm_score(delta: Optional[float]) -> float:
    """
    OTM contracts with unusual flow signal more conviction (0–10 points).
    Deep OTM sweeps = very bullish/bearish signal.
    """
    if delta is None or math.isnan(delta):
        return 5.0   # neutral default
    abs_delta = abs(delta)
    if abs_delta < 0.15:      # deep OTM
        return 10.0
    elif abs_delta < 0.30:    # OTM
        return 7.0
    elif abs_delta < 0.55:    # near-ATM
        return 5.0
    return 2.0                # deep ITM – less signal significance


def score_contract_flow(
    volume: int,
    open_interest: int,
    dte: int,
    mid_price: float,
    delta: Optional[float] = None,
) -> float:
    """
    Score a single contract's flow (0–100).

    Breakdown:
      40 pts – volume / OI ratio
      30 pts – total premium swept
      20 pts – expiry urgency (short DTE = aggressive)
      10 pts – OTM conviction signal
    """
    if volume < config.MIN_OPTION_VOLUME:
        return 0.0
    score = (
        _vol_oi_score(volume, open_interest)
        + _premium_score(volume, mid_price)
        + _expiry_urgency_score(dte)
        + _otm_score(delta)
    )
    return round(min(score, 100.0), 1)


# ─────────────────────────────────────────────────────────────────────────────
# Chain-level analysis
# ─────────────────────────────────────────────────────────────────────────────

def detect_unusual_flow(chain_df: pd.DataFrame) -> pd.DataFrame:
    """
    Score every contract in the options chain for unusual flow.

    Adds columns:
        flow_score        : 0–100 score for this contract
        total_premium     : total $ premium (volume × mid × 100)
        vol_oi_ratio      : volume / open_interest
        is_sweep          : bool – high-conviction unusual flag
        flow_direction    : "call_bullish" | "put_bearish" | "neutral"

    Returns the full DataFrame sorted by flow_score descending.
    """
    if chain_df.empty:
        return chain_df

    df = chain_df.copy()

    flow_scores, premiums, vol_oi_ratios, is_sweep, direction = [], [], [], [], []

    for _, row in df.iterrows():
        vol = int(row.get("volume", 0))
        oi = int(row.get("openInterest", 0))
        dte = int(row.get("dte", 30))
        mid = float(row.get("mid", 0))
        otype = str(row.get("option_type", "call")).lower()
        delta_val = row.get("bs_delta") or row.get("delta")
        if delta_val is not None:
            try:
                delta_val = float(delta_val)
            except (TypeError, ValueError):
                delta_val = None

        fs = score_contract_flow(vol, oi, dte, mid, delta_val)
        total_prem = vol * mid * 100
        ratio = vol / max(oi, 1)
        sweep = (
            fs >= 60
            and total_prem >= config.UNUSUAL_FLOW_MIN_PREMIUM
            and ratio >= config.UNUSUAL_FLOW_VOL_OI_RATIO
        )
        if otype == "call":
            dir_str = "call_bullish"
        else:
            dir_str = "put_bearish"

        flow_scores.append(fs)
        premiums.append(round(total_prem, 2))
        vol_oi_ratios.append(round(ratio, 2))
        is_sweep.append(sweep)
        direction.append(dir_str)

    df["flow_score"] = flow_scores
    df["total_premium"] = premiums
    df["vol_oi_ratio"] = vol_oi_ratios
    df["is_sweep"] = is_sweep
    df["flow_direction"] = direction

    return df.sort_values("flow_score", ascending=False).reset_index(drop=True)


def ticker_flow_summary(chain_df: pd.DataFrame) -> dict:
    """
    Aggregate flow summary for the whole ticker.

    Returns:
        {
          "ticker_flow_score"    : float  – 0-100 overall ticker score
          "dominant_direction"   : str    – "bullish" | "bearish" | "neutral"
          "num_unusual_contracts": int
          "num_sweeps"           : int
          "top_premium_contract" : str    – contract symbol of biggest bet
          "total_call_premium"   : float
          "total_put_premium"    : float
          "call_put_ratio"       : float  – > 1 bullish, < 1 bearish
        }
    """
    if chain_df.empty or "flow_score" not in chain_df.columns:
        return {}

    unusual = chain_df[chain_df["flow_score"] >= 40]
    num_unusual = len(unusual)
    num_sweeps = int(chain_df["is_sweep"].sum()) if "is_sweep" in chain_df.columns else 0

    # Ticker-level flow score: max single score + bonus for multiple unusual contracts
    max_score = float(chain_df["flow_score"].max())
    multi_bonus = min(num_unusual * 3, 20)   # up to +20 for many unusual contracts
    ticker_score = min(max_score + multi_bonus, 100.0)

    # Call vs put premium battle
    calls = chain_df[chain_df["option_type"] == "call"]
    puts = chain_df[chain_df["option_type"] == "put"]
    call_prem = float(calls["total_premium"].sum()) if "total_premium" in calls.columns else 0.0
    put_prem = float(puts["total_premium"].sum()) if "total_premium" in puts.columns else 0.0
    cp_ratio = call_prem / max(put_prem, 0.01)

    if cp_ratio > 1.5:
        direction = "bullish"
    elif cp_ratio < 0.67:
        direction = "bearish"
    else:
        direction = "neutral"

    top_row = chain_df.nlargest(1, "total_premium")
    top_contract = str(top_row["contractSymbol"].iloc[0]) if not top_row.empty else ""

    return {
        "ticker_flow_score": round(ticker_score, 1),
        "dominant_direction": direction,
        "num_unusual_contracts": num_unusual,
        "num_sweeps": num_sweeps,
        "top_premium_contract": top_contract,
        "total_call_premium": round(call_prem, 2),
        "total_put_premium": round(put_prem, 2),
        "call_put_ratio": round(cp_ratio, 2),
    }
