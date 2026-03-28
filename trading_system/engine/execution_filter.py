"""
engine/execution_filter.py – Execution quality gate.

This is the MOST CRITICAL filter for options scalping.
A correct signal with a bad contract still loses money.

Answers the three execution questions:
  1. Is the spread too wide?         → reject / warn
  2. Will this order fill cleanly?   → fill quality estimate
  3. Are you overpaying premium?     → IV vs HV premium check

Usage:
    result = check_execution(contract_row, iv_stats)
    if result["approved"]:
        place_order(...)
"""

import logging
import math
from typing import Optional

import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

logger = logging.getLogger(__name__)


def _safe_float(val, default: float = 0.0) -> float:
    try:
        v = float(val)
        return v if not math.isnan(v) else default
    except (TypeError, ValueError):
        return default


def check_execution(
    contract: pd.Series,
    iv_stats: Optional[dict] = None,
) -> dict:
    """
    Full execution quality check for a single contract.

    Args:
        contract  : a row from the options-chain DataFrame
        iv_stats  : dict from iv_calculator.compute_iv_stats()
                    (used for IV premium check; optional)

    Returns:
        {
          "approved"       : bool  – True if all checks pass
          "exec_score"     : float – 0-100 combined execution score
          "reasons"        : list[str] – list of fail reasons
          "warnings"       : list[str] – non-blocking cautions
          "fill_estimate"  : float – estimated fill price
          "spread_pct"     : float – spread as % of mid
          "premium_check"  : str   – "fair" / "elevated" / "expensive"
        }
    """
    reasons = []
    warnings = []

    bid = _safe_float(contract.get("bid"))
    ask = _safe_float(contract.get("ask"))
    mid = _safe_float(contract.get("mid")) or ((bid + ask) / 2)
    spread = ask - bid
    volume = int(_safe_float(contract.get("volume")))
    oi = int(_safe_float(contract.get("openInterest")))

    spread_pct = (spread / mid) if mid > 0 else 999.0

    # ── Hard reject: no market ─────────────────────────────────────────────
    if ask <= 0:
        reasons.append("No ask price – market is not open for this contract")

    # ── Hard reject: spread too wide ───────────────────────────────────────
    if spread > config.MAX_SPREAD_ABS:
        reasons.append(
            f"Spread ${spread:.2f} > max ${config.MAX_SPREAD_ABS:.2f} – too wide to scalp"
        )
    elif spread_pct > config.MAX_SPREAD_PCT:
        reasons.append(
            f"Spread {spread_pct*100:.1f}% > max {config.MAX_SPREAD_PCT*100:.0f}% – too expensive"
        )

    # ── Hard reject: insufficient liquidity ───────────────────────────────
    if volume < config.MIN_OPTION_VOLUME:
        reasons.append(
            f"Volume {volume} < minimum {config.MIN_OPTION_VOLUME} – insufficient activity"
        )
    if oi < config.MIN_OPEN_INTEREST:
        reasons.append(
            f"Open interest {oi} < minimum {config.MIN_OPEN_INTEREST} – too illiquid"
        )

    # ── Warning: elevated spread ───────────────────────────────────────────
    if not reasons and spread_pct > config.MAX_SPREAD_PCT * 0.6:
        warnings.append(
            f"Spread {spread_pct*100:.1f}% is approaching max threshold – size conservatively"
        )

    # ── IV premium check (optional, advisory only) ─────────────────────────
    premium_check = "unknown"
    if iv_stats:
        current_iv = _safe_float(iv_stats.get("current_iv", 0)) / 100   # convert % back
        hv30 = _safe_float(iv_stats.get("hv30", 0)) / 100
        contract_iv = _safe_float(contract.get("computed_iv") or contract.get("impliedVolatility"))

        iv_to_check = contract_iv if contract_iv > 0 else current_iv
        if hv30 > 0 and iv_to_check > 0:
            iv_hv_ratio = iv_to_check / hv30
            if iv_hv_ratio < 1.1:
                premium_check = "fair"
            elif iv_hv_ratio < 1.5:
                premium_check = "elevated"
                warnings.append(
                    f"IV ({iv_to_check*100:.0f}%) is {iv_hv_ratio:.1f}× HV30 ({hv30*100:.0f}%) "
                    f"– you may be overpaying for premium"
                )
            else:
                premium_check = "expensive"
                warnings.append(
                    f"IV ({iv_to_check*100:.0f}%) is {iv_hv_ratio:.1f}× HV30 ({hv30*100:.0f}%) "
                    f"– premium is very expensive; consider a spread instead"
                )
        else:
            premium_check = "fair"

    # ── Execution score ────────────────────────────────────────────────────
    spread_score = _safe_float(contract.get("spread_score"), 50.0)
    liq_score = _safe_float(contract.get("liquidity_score"), 50.0)
    exec_score = (0.55 * spread_score + 0.45 * liq_score) if not reasons else 0.0

    # Reduce score for warnings
    exec_score = max(0.0, exec_score - len(warnings) * 5)

    # Fill estimate (slippage-adjusted)
    fill_ratio = min(spread_pct / max(config.MAX_SPREAD_PCT, 0.001), 1.0)
    fill_estimate = mid + fill_ratio * (spread / 2)

    return {
        "approved": len(reasons) == 0,
        "exec_score": round(exec_score, 1),
        "reasons": reasons,
        "warnings": warnings,
        "fill_estimate": round(fill_estimate, 3),
        "spread_pct": round(spread_pct * 100, 2),
        "premium_check": premium_check,
    }


def execution_summary_table(chain_df: pd.DataFrame, iv_stats: Optional[dict] = None) -> pd.DataFrame:
    """
    Run execution_check on every row and return a summary DataFrame.
    Columns: contractSymbol, approved, exec_score, spread_pct, fill_estimate, premium_check, reasons
    """
    rows = []
    for _, contract in chain_df.iterrows():
        result = check_execution(contract, iv_stats)
        rows.append({
            "contractSymbol": contract.get("contractSymbol", ""),
            "option_type": contract.get("option_type", ""),
            "strike": contract.get("strike", ""),
            "dte": contract.get("dte", ""),
            "mid": contract.get("mid", ""),
            "approved": result["approved"],
            "exec_score": result["exec_score"],
            "spread_pct": result["spread_pct"],
            "fill_estimate": result["fill_estimate"],
            "premium_check": result["premium_check"],
            "reasons": "; ".join(result["reasons"]) if result["reasons"] else "OK",
        })
    return pd.DataFrame(rows)
