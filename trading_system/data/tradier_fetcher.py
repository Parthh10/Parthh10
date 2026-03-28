"""
data/tradier_fetcher.py – Tradier free developer API layer.

What you get for FREE:
  • Sandbox account : https://developer.tradier.com/
      → Always free, near real-time data (paper data, very close to live)
  • Brokerage account (live) : free with a funded Tradier account
      → Full real-time options Greeks from ORATS

Registration:
  1. Go to https://developer.tradier.com/
  2. Click "Get an API Key"
  3. Copy your sandbox token into .env as TRADIER_TOKEN=<token>
  4. Set TRADIER_SANDBOX=true   (or false for live)

Endpoints used:
  GET /v1/markets/options/chains   → full chain + Greeks (delta/gamma/theta/vega/rho/iv)
  GET /v1/markets/options/expirations
  GET /v1/markets/quotes           → real-time quote for the underlying
  GET /v1/markets/options/strikes  → available strikes for an expiry
"""

import logging
from typing import Optional

import pandas as pd
import requests

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({
    "Accept": "application/json",
})


def _auth_headers() -> dict:
    token = config.TRADIER_TOKEN
    if not token:
        raise EnvironmentError(
            "TRADIER_TOKEN is not set. "
            "Register at https://developer.tradier.com/ and set it in your .env file."
        )
    return {"Authorization": f"Bearer {token}"}


def _get(path: str, params: Optional[dict] = None) -> dict:
    url = f"{config.TRADIER_BASE_URL}{path}"
    try:
        r = _SESSION.get(url, headers=_auth_headers(), params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as exc:
        logger.error("Tradier HTTP %s: %s", exc.response.status_code, exc.response.text)
        return {}
    except Exception as exc:
        logger.error("Tradier request failed: %s", exc)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_expirations(ticker: str, include_all_roots: bool = False) -> list[str]:
    """Return available expiry dates for a ticker."""
    data = _get(
        "/markets/options/expirations",
        {"symbol": ticker, "includeAllRoots": str(include_all_roots).lower()},
    )
    dates = data.get("expirations", {}) or {}
    return dates.get("date", []) or []


def get_options_chain(ticker: str, expiry: str) -> pd.DataFrame:
    """
    Fetch full options chain for one expiry, including ORATS-powered Greeks.

    Returns DataFrame with columns (among others):
        symbol, option_type, strike, bid, ask, mid, spread, spread_pct,
        volume, open_interest, greeks.delta, greeks.gamma,
        greeks.theta, greeks.vega, greeks.rho, greeks.mid_iv
    """
    data = _get(
        "/markets/options/chains",
        {"symbol": ticker, "expiration": expiry, "greeks": "true"},
    )
    options = (data.get("options") or {}).get("option") or []
    if not options:
        logger.warning("Tradier: no chain data for %s %s", ticker, expiry)
        return pd.DataFrame()

    rows = []
    for opt in options:
        greeks = opt.get("greeks") or {}
        rows.append({
            "ticker": ticker,
            "expiry": expiry,
            "contractSymbol": opt.get("symbol"),
            "option_type": opt.get("option_type"),   # "call" | "put"
            "strike": float(opt.get("strike", 0)),
            "bid": float(opt.get("bid") or 0),
            "ask": float(opt.get("ask") or 0),
            "lastPrice": float(opt.get("last") or 0),
            "volume": int(opt.get("volume") or 0),
            "openInterest": int(opt.get("open_interest") or 0),
            "impliedVolatility": greeks.get("mid_iv"),   # decimal (0.35 = 35%)
            "delta": greeks.get("delta"),
            "gamma": greeks.get("gamma"),
            "theta": greeks.get("theta"),
            "vega": greeks.get("vega"),
            "rho": greeks.get("rho"),
            "smv_vol": greeks.get("smv_vol"),  # ORATS smile-adjusted vol
        })

    df = pd.DataFrame(rows)
    df["mid"] = (df["bid"] + df["ask"]) / 2
    df["spread"] = df["ask"] - df["bid"]
    df["spread_pct"] = df.apply(
        lambda r: (r["spread"] / r["mid"] * 100) if r["mid"] > 0 else 999.0, axis=1
    )
    return df


def get_underlying_quote(ticker: str) -> dict:
    """Return real-time quote for the underlying security."""
    data = _get("/markets/quotes", {"symbols": ticker, "greeks": "false"})
    quotes = (data.get("quotes") or {}).get("quote") or {}
    # API returns a list if multiple tickers, a dict if single
    if isinstance(quotes, list):
        quotes = quotes[0] if quotes else {}
    return {
        "ticker": quotes.get("symbol"),
        "price": float(quotes.get("last") or quotes.get("ask") or 0),
        "bid": float(quotes.get("bid") or 0),
        "ask": float(quotes.get("ask") or 0),
        "volume": int(quotes.get("volume") or 0),
        "change_pct": float(quotes.get("change_percentage") or 0),
    }


def is_available() -> bool:
    """Return True if a Tradier token is configured."""
    return bool(config.TRADIER_TOKEN)
