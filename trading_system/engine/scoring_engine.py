"""
engine/scoring_engine.py – Master composite scoring system (0–100).

Combines four independent signal dimensions into one actionable score:

  ┌────────────────┬────────┬──────────────────────────────────────────────┐
  │ Dimension      │ Weight │ Source                                       │
  ├────────────────┼────────┼──────────────────────────────────────────────┤
  │ Flow           │  35 %  │ flow_detector.ticker_flow_summary            │
  │ Execution      │  30 %  │ execution_filter.check_execution             │
  │ Greeks         │  20 %  │ delta target + theta quality                 │
  │ IV             │  15 %  │ iv_calculator.compute_iv_stats               │
  └────────────────┴────────┴──────────────────────────────────────────────┘

Score Interpretation:
  80–100 : Strong signal – all dimensions align
  60–79  : Good signal   – proceed with standard sizing
  40–59  : Weak/mixed    – reduce size or wait for confirmation
  0–39   : Avoid         – do not trade

Usage:
    score = compute_composite_score(
        ticker_flow=flow_summary,
        best_contract=contract_row,
        exec_result=execution_check_result,
        iv_stats=iv_stats,
    )
"""

import logging
import math
from typing import Optional

import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

logger = logging.getLogger(__name__)


def _safe(val, default: float = 0.0) -> float:
    try:
        v = float(val)
        return v if not math.isnan(v) else default
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────────────────────
# Individual dimension scorers
# ─────────────────────────────────────────────────────────────────────────────

def _flow_dimension_score(ticker_flow: dict) -> float:
    """Extract the flow score from ticker_flow_summary output."""
    return _safe(ticker_flow.get("ticker_flow_score", 0))


def _execution_dimension_score(exec_result: dict) -> float:
    """Use exec_score if approved; zero if rejected."""
    if not exec_result.get("approved", False):
        return 0.0
    return _safe(exec_result.get("exec_score", 0))


def _greeks_dimension_score(
    best_contract: Optional[pd.Series],
    direction: str = "call",
) -> float:
    """
    Score the Greeks quality of the best selected contract.

    Scores:
      • Delta proximity to ideal range (0-60 pts)
      • Theta quality: decay not too severe  (0-40 pts)
    """
    if best_contract is None:
        return 0.0

    # Delta sub-score
    delta_val = None
    for col in ("bs_delta", "delta"):
        v = best_contract.get(col)
        if v is not None:
            try:
                delta_val = float(v)
                break
            except (TypeError, ValueError):
                pass

    lo, hi = config.TARGET_DELTA_RANGE
    ideal = (lo + hi) / 2
    if delta_val is not None and not math.isnan(delta_val):
        abs_d = abs(delta_val)
        if lo <= abs_d <= hi:
            # Full credit, closer to ideal = bonus
            delta_sub = 60 * (1 - abs(abs_d - ideal) / max(hi - lo, 0.01) * 0.5)
        else:
            # Partial credit for being just outside range
            distance = min(abs(abs_d - lo), abs(abs_d - hi))
            delta_sub = max(0, 60 * (1 - distance / 0.2))
    else:
        delta_sub = 30.0   # neutral

    # Theta sub-score
    theta_val = None
    for col in ("bs_theta", "theta"):
        v = best_contract.get(col)
        if v is not None:
            try:
                theta_val = float(v)
                break
            except (TypeError, ValueError):
                pass

    if theta_val is not None and not math.isnan(theta_val):
        # theta is negative; less negative = better for buyers
        if theta_val >= config.MIN_THETA:
            theta_sub = 40.0 * (1 - min(abs(theta_val) / abs(config.MIN_THETA), 1.0))
        else:
            theta_sub = 0.0   # theta too severe
    else:
        theta_sub = 20.0   # neutral

    return round(min(delta_sub + theta_sub, 100.0), 1)


def _iv_dimension_score(iv_stats: dict, direction: str = "call") -> float:
    """
    Score IV conditions.

    For BUYERS (scalpers buying calls/puts):
      Low IVR  → good (cheap premium)
      High IVR → bad  (expensive premium)

    Score is inversely proportional to IVR for buyers.
    """
    if not iv_stats:
        return 50.0   # neutral when unknown

    ivr = _safe(iv_stats.get("iv_rank", 50))
    regime = iv_stats.get("iv_regime", "normal")

    if regime == "low":
        return 85.0
    elif regime == "normal":
        # Linear scale: IVR 20→50 maps to 85→55
        return max(55.0, 85.0 - 1.0 * (ivr - 20))
    elif regime == "elevated":
        return max(35.0, 55.0 - 0.8 * (ivr - 50))
    else:  # extreme
        return max(10.0, 35.0 - 0.5 * (ivr - 80))


# ─────────────────────────────────────────────────────────────────────────────
# Master composite scorer
# ─────────────────────────────────────────────────────────────────────────────

def compute_composite_score(
    ticker_flow: dict,
    exec_result: dict,
    best_contract: Optional[pd.Series] = None,
    iv_stats: Optional[dict] = None,
    direction: str = "call",
) -> dict:
    """
    Compute the master composite trade score and return a full breakdown.

    Returns:
        {
          "composite_score"  : float  – 0-100
          "flow_score"       : float
          "execution_score"  : float
          "greeks_score"     : float
          "iv_score"         : float
          "verdict"          : str    – "TRADE" / "WATCH" / "SKIP"
          "dominant_direction": str
          "score_breakdown"  : dict
        }
    """
    w = config.SCORE_WEIGHTS
    flow_s = _flow_dimension_score(ticker_flow)
    exec_s = _execution_dimension_score(exec_result)
    greeks_s = _greeks_dimension_score(best_contract, direction)
    iv_s = _iv_dimension_score(iv_stats or {}, direction)

    composite = (
        w["flow"] * flow_s
        + w["execution"] * exec_s
        + w["greeks"] * greeks_s
        + w["iv"] * iv_s
    )

    # Hard-gate: if execution is rejected, composite cannot exceed 30
    if not exec_result.get("approved", False):
        composite = min(composite, 30.0)

    # Hard-gate: if flow score below minimum, cap composite
    if flow_s < config.MIN_FLOW_SCORE:
        composite = min(composite, 50.0)

    composite = round(composite, 1)

    if composite >= 75:
        verdict = "STRONG BUY"
    elif composite >= config.MIN_COMPOSITE_SCORE:
        verdict = "BUY"
    elif composite >= 45:
        verdict = "WATCH"
    else:
        verdict = "SKIP"

    return {
        "composite_score": composite,
        "flow_score": round(flow_s, 1),
        "execution_score": round(exec_s, 1),
        "greeks_score": round(greeks_s, 1),
        "iv_score": round(iv_s, 1),
        "verdict": verdict,
        "dominant_direction": ticker_flow.get("dominant_direction", "unknown"),
        "num_sweeps": ticker_flow.get("num_sweeps", 0),
        "score_breakdown": {
            "flow":       {"score": round(flow_s, 1),   "weight": w["flow"]},
            "execution":  {"score": round(exec_s, 1),   "weight": w["execution"]},
            "greeks":     {"score": round(greeks_s, 1), "weight": w["greeks"]},
            "iv":         {"score": round(iv_s, 1),     "weight": w["iv"]},
        },
    }


def score_label(score: float) -> str:
    """Return a coloured ASCII label for terminal output."""
    if score >= 75:
        return f"[★★★ {score:.0f}]"
    elif score >= 60:
        return f"[★★  {score:.0f}]"
    elif score >= 45:
        return f"[★   {score:.0f}]"
    else:
        return f"[    {score:.0f}]"
