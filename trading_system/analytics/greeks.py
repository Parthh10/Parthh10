"""
analytics/greeks.py – Black-Scholes Greeks calculator (100 % free, no API).

All five first-order Greeks + implied volatility solver:
    delta   – price sensitivity to underlying move
    gamma   – delta sensitivity (convexity)
    theta   – time decay (daily, in dollars per share)
    vega    – sensitivity to 1-point IV change
    rho     – sensitivity to interest rate change

Implied volatility is solved via Brent's method (scipy.optimize.brentq)
which is numerically stable and fast.

Usage:
    from analytics.greeks import price_option, all_greeks, implied_vol

    g = all_greeks(S=175, K=180, T=30/365, r=0.053, sigma=0.35, option_type='call')
    iv = implied_vol(market_price=3.40, S=175, K=180, T=30/365, r=0.053, option_type='call')
"""

import logging
import math
from typing import Literal

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

logger = logging.getLogger(__name__)

OptionType = Literal["call", "put"]

# Small guard against log(0) or sqrt(0) in edge cases
_EPS = 1e-10


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    """Standard BSM d1 and d2 parameters."""
    sqrt_T = math.sqrt(max(T, _EPS))
    log_SK = math.log(max(S / K, _EPS))
    d1 = (log_SK + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return d1, d2


def price_option(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType = "call",
) -> float:
    """
    Black-Scholes theoretical price.

    Args:
        S     : underlying spot price
        K     : strike price
        T     : time to expiry in years  (e.g. 30/365)
        r     : risk-free rate (annual, decimal – e.g. 0.053)
        sigma : volatility (annual, decimal – e.g. 0.30 for 30%)
        option_type : 'call' or 'put'
    """
    if T <= 0:
        # Intrinsic value at expiry
        if option_type == "call":
            return max(S - K, 0.0)
        return max(K - S, 0.0)

    d1, d2 = _d1_d2(S, K, T, r, sigma)
    discount = math.exp(-r * T)

    if option_type == "call":
        return S * norm.cdf(d1) - K * discount * norm.cdf(d2)
    return K * discount * norm.cdf(-d2) - S * norm.cdf(-d1)


def delta(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType = "call",
) -> float:
    """Delta: dPrice/dS.  Range: [0,1] calls, [-1,0] puts."""
    if T <= 0:
        if option_type == "call":
            return 1.0 if S >= K else 0.0
        return -1.0 if S <= K else 0.0
    d1, _ = _d1_d2(S, K, T, r, sigma)
    if option_type == "call":
        return float(norm.cdf(d1))
    return float(norm.cdf(d1) - 1)


def gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Gamma: d²Price/dS² (same for calls and puts)."""
    if T <= 0:
        return 0.0
    d1, _ = _d1_d2(S, K, T, r, sigma)
    return float(norm.pdf(d1) / (S * sigma * math.sqrt(T)))


def theta(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType = "call",
) -> float:
    """
    Theta: daily time decay (divided by 365 → per calendar day, per share).
    Negative for long options.
    """
    if T <= 0:
        return 0.0
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    sqrt_T = math.sqrt(T)
    discount = math.exp(-r * T)
    common = -(S * norm.pdf(d1) * sigma) / (2 * sqrt_T)
    if option_type == "call":
        val = common - r * K * discount * norm.cdf(d2)
    else:
        val = common + r * K * discount * norm.cdf(-d2)
    return float(val / 365)   # daily decay


def vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Vega: sensitivity to a 1 % move in IV (divided by 100 internally).
    Reported as dollar change per 1-point IV move (same for calls and puts).
    """
    if T <= 0:
        return 0.0
    d1, _ = _d1_d2(S, K, T, r, sigma)
    return float(S * norm.pdf(d1) * math.sqrt(T) / 100)


def rho(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType = "call",
) -> float:
    """Rho: sensitivity to a 1 % move in risk-free rate (divided by 100)."""
    if T <= 0:
        return 0.0
    _, d2 = _d1_d2(S, K, T, r, sigma)
    discount = math.exp(-r * T)
    if option_type == "call":
        return float(K * T * discount * norm.cdf(d2) / 100)
    return float(-K * T * discount * norm.cdf(-d2) / 100)


def all_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType = "call",
) -> dict[str, float]:
    """Return all five Greeks + theoretical price in one call."""
    return {
        "price": price_option(S, K, T, r, sigma, option_type),
        "delta": delta(S, K, T, r, sigma, option_type),
        "gamma": gamma(S, K, T, r, sigma),
        "theta": theta(S, K, T, r, sigma, option_type),
        "vega": vega(S, K, T, r, sigma),
        "rho": rho(S, K, T, r, sigma, option_type),
    }


def implied_vol(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: OptionType = "call",
    low: float = 1e-4,
    high: float = 10.0,
) -> float:
    """
    Solve for the implied volatility that matches the observed market price.

    Uses Brent's method – robust and fast.  Returns NaN if no solution found.
    """
    if T <= _EPS or market_price <= 0:
        return float("nan")

    def objective(sigma: float) -> float:
        return price_option(S, K, T, r, sigma, option_type) - market_price

    try:
        # Check bracket feasibility
        if objective(low) * objective(high) > 0:
            return float("nan")
        return float(brentq(objective, low, high, xtol=1e-6, maxiter=200))
    except (ValueError, RuntimeError) as exc:
        logger.debug("IV solver failed S=%s K=%s T=%s: %s", S, K, T, exc)
        return float("nan")


def enrich_chain_with_greeks(
    df,
    underlying_price: float,
    risk_free_rate: float = 0.053,
) -> "pd.DataFrame":
    """
    Add computed Greeks to an options-chain DataFrame (from yfinance or Tradier).

    Expects columns: strike, dte, mid, option_type, impliedVolatility.
    Adds columns: bs_delta, bs_gamma, bs_theta, bs_vega, bs_rho, computed_iv.
    Uses IV from the API where present; falls back to computing IV from mid-price.
    """
    import pandas as pd

    if df.empty:
        return df

    results = {
        "bs_delta": [],
        "bs_gamma": [],
        "bs_theta": [],
        "bs_vega": [],
        "bs_rho": [],
        "computed_iv": [],
    }

    for _, row in df.iterrows():
        K = float(row["strike"])
        T = max(float(row.get("dte", 1)), 1) / 365
        otype = str(row.get("option_type", "call")).lower()
        iv = row.get("impliedVolatility") or row.get("smv_vol")

        if pd.isna(iv) or iv is None or float(iv) <= 0:
            mid = float(row.get("mid", 0))
            iv = implied_vol(mid, underlying_price, K, T, risk_free_rate, otype)

        try:
            iv_f = float(iv)
        except (TypeError, ValueError):
            iv_f = float("nan")

        if math.isnan(iv_f) or iv_f <= 0:
            results["bs_delta"].append(float("nan"))
            results["bs_gamma"].append(float("nan"))
            results["bs_theta"].append(float("nan"))
            results["bs_vega"].append(float("nan"))
            results["bs_rho"].append(float("nan"))
            results["computed_iv"].append(float("nan"))
            continue

        g = all_greeks(underlying_price, K, T, risk_free_rate, iv_f, otype)
        results["bs_delta"].append(g["delta"])
        results["bs_gamma"].append(g["gamma"])
        results["bs_theta"].append(g["theta"])
        results["bs_vega"].append(g["vega"])
        results["bs_rho"].append(g["rho"])
        results["computed_iv"].append(iv_f)

    df = df.copy()
    for col, vals in results.items():
        df[col] = vals

    return df
