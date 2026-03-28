"""
engine/contract_selector.py – Optimal strike & expiry selection.

Selects the best tradeable contract from a screened options chain using:
  1. Delta target window      (near ATM with directional edge)
  2. DTE window               (short-dated but not day-of-expiry)
  3. Theta decay rate         (don't buy contracts bleeding too fast)
  4. Liquidity filter         (volume + OI minimums)
  5. Execution filter         (spread must be within policy)
  6. Composite ranking        (multi-factor score)

Usage:
    best = select_best_contract(chain_df, direction="call")
    ranked = rank_contracts(chain_df, direction="call")
"""

import logging
import math
from typing import Optional

import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

logger = logging.getLogger(__name__)


def _has_col(df: pd.DataFrame, col: str) -> bool:
    return col in df.columns


def filter_tradeable(chain_df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply hard filters:
      • DTE within [MIN_DTE, MAX_DTE]
      • Volume >= MIN_OPTION_VOLUME
      • Open interest >= MIN_OPEN_INTEREST
      • Spread within policy (if spread columns available)
    """
    df = chain_df.copy()

    if _has_col(df, "dte"):
        df = df[(df["dte"] >= config.MIN_DTE) & (df["dte"] <= config.MAX_DTE)]

    if _has_col(df, "volume"):
        df = df[df["volume"] >= config.MIN_OPTION_VOLUME]

    if _has_col(df, "openInterest"):
        df = df[df["openInterest"] >= config.MIN_OPEN_INTEREST]

    if _has_col(df, "tradeable"):
        df = df[df["tradeable"]]
    elif _has_col(df, "spread_pct"):
        df = df[df["spread_pct"] <= config.MAX_SPREAD_PCT * 100]

    logger.debug("filter_tradeable: %d → %d contracts", len(chain_df), len(df))
    return df.reset_index(drop=True)


def filter_by_direction(chain_df: pd.DataFrame, direction: str) -> pd.DataFrame:
    """Filter to calls (bullish) or puts (bearish)."""
    d = direction.lower()
    if "call" in d or "bull" in d:
        return chain_df[chain_df["option_type"] == "call"].reset_index(drop=True)
    if "put" in d or "bear" in d:
        return chain_df[chain_df["option_type"] == "put"].reset_index(drop=True)
    return chain_df   # both


def filter_by_delta(chain_df: pd.DataFrame) -> pd.DataFrame:
    """Keep contracts within the target delta range from config."""
    delta_col = _delta_col(chain_df)
    if delta_col is None:
        return chain_df

    lo, hi = config.TARGET_DELTA_RANGE
    df = chain_df.copy()
    df["abs_delta"] = df[delta_col].abs()
    df = df[(df["abs_delta"] >= lo) & (df["abs_delta"] <= hi)]
    return df.reset_index(drop=True)


def filter_by_theta(chain_df: pd.DataFrame) -> pd.DataFrame:
    """Remove contracts with excessive theta decay."""
    theta_col = _theta_col(chain_df)
    if theta_col is None:
        return chain_df
    return chain_df[chain_df[theta_col] >= config.MIN_THETA].reset_index(drop=True)


def _delta_col(df: pd.DataFrame) -> Optional[str]:
    for col in ("bs_delta", "delta"):
        if col in df.columns:
            return col
    return None


def _theta_col(df: pd.DataFrame) -> Optional[str]:
    for col in ("bs_theta", "theta"):
        if col in df.columns:
            return col
    return None


def rank_contracts(
    chain_df: pd.DataFrame,
    direction: str = "call",
) -> pd.DataFrame:
    """
    Apply all filters then rank remaining contracts by composite score.

    Composite = weighted combination of:
      • flow_score       (if present)
      • spread_score     (if present)
      • liquidity_score  (if present)
      • delta proximity  (how close to ideal delta)
      • dte preference   (sweet spot ~ 14-30 DTE)

    Returns sorted DataFrame, best contract first.
    """
    df = filter_tradeable(chain_df)
    df = filter_by_direction(df, direction)
    df = filter_by_delta(df)
    df = filter_by_theta(df)

    if df.empty:
        logger.warning("No tradeable contracts after filtering (direction=%s)", direction)
        return df

    # ── Delta proximity score (max 30 pts) ────────────────────────────────
    delta_col = _delta_col(df)
    ideal_delta = sum(config.TARGET_DELTA_RANGE) / 2   # mid of target range
    if delta_col:
        df = df.copy()
        df["delta_prox_score"] = df[delta_col].abs().apply(
            lambda d: max(0, 30 * (1 - abs(d - ideal_delta) / 0.25))
            if not math.isnan(d) else 15.0
        )
    else:
        df["delta_prox_score"] = 15.0

    # ── DTE sweet-spot score (max 20 pts) ─────────────────────────────────
    if _has_col(df, "dte"):
        sweet_dte = 21   # ideal DTE for scalping
        df["dte_score"] = df["dte"].apply(
            lambda d: max(0, 20 * (1 - abs(d - sweet_dte) / sweet_dte))
        )
    else:
        df["dte_score"] = 10.0

    # ── Assemble composite score ───────────────────────────────────────────
    components = {
        "flow_score": 0.30,
        "spread_score": 0.25,
        "liquidity_score": 0.15,
        "delta_prox_score": 0.20,
        "dte_score": 0.10,
    }
    df["contract_score"] = 0.0
    for col, weight in components.items():
        if _has_col(df, col):
            df["contract_score"] += df[col].fillna(0) * weight
        else:
            # If column missing, share its weight equally among present columns
            pass  # score stays at 0 for this component

    df = df.sort_values("contract_score", ascending=False).reset_index(drop=True)
    return df


def select_best_contract(
    chain_df: pd.DataFrame,
    direction: str = "call",
) -> Optional[pd.Series]:
    """
    Return the single best tradeable contract as a pd.Series,
    or None if no qualifying contract exists.
    """
    ranked = rank_contracts(chain_df, direction)
    if ranked.empty:
        return None
    best = ranked.iloc[0]
    logger.info(
        "Best %s contract: %s  score=%.1f  delta=%.2f  DTE=%d  mid=%.2f",
        direction,
        best.get("contractSymbol", "?"),
        best.get("contract_score", 0),
        abs(float(best.get(_delta_col(ranked) or "bs_delta", 0) or 0)),
        int(best.get("dte", 0)),
        float(best.get("mid", 0)),
    )
    return best
