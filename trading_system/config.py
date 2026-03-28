"""
config.py – Central configuration for the free-tier options trading system.

How to set API keys (never hard-code them):
  1. Copy .env.example → .env
  2. Fill in your keys
  3. Run:  python main.py

Free API registrations required:
  • Tradier Developer (free):  https://developer.tradier.com/
      → sandbox token  (paper data, always free)
      → live token     (real-time data, free with a Tradier brokerage account)
"""

import os
from dotenv import load_dotenv

load_dotenv()  # reads .env in project root if present

# ─────────────────────────────────────────────────────────────────────────────
# DATA SOURCE TOKENS
# ─────────────────────────────────────────────────────────────────────────────

# Tradier – free developer / brokerage account
# Sandbox (always free, slight data delay):  https://sandbox.tradier.com/v1
# Production (real-time, free with account): https://api.tradier.com/v1
TRADIER_TOKEN: str = os.getenv("TRADIER_TOKEN", "")
TRADIER_USE_SANDBOX: bool = os.getenv("TRADIER_SANDBOX", "true").lower() == "true"
TRADIER_BASE_URL: str = (
    "https://sandbox.tradier.com/v1"
    if TRADIER_USE_SANDBOX
    else "https://api.tradier.com/v1"
)

# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSE – tickers to scan
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_TICKERS: list[str] = [
    "SPY", "QQQ", "AAPL", "TSLA", "NVDA",
    "MSFT", "AMZN", "META", "AMD", "IWM",
]

# ─────────────────────────────────────────────────────────────────────────────
# GREEKS / PRICING
# ─────────────────────────────────────────────────────────────────────────────
RISK_FREE_RATE: float = 0.053    # ~10-year Treasury note yield; update periodically
DEFAULT_DIVIDEND_YIELD: float = 0.0

# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION QUALITY THRESHOLDS
# ─────────────────────────────────────────────────────────────────────────────
MAX_SPREAD_PCT: float = 0.10          # reject if spread > 10 % of mid-price
MAX_SPREAD_ABS: float = 0.50          # reject if raw spread > $0.50
MIN_OPTION_VOLUME: int = 50           # minimum daily volume
MIN_OPEN_INTEREST: int = 200          # minimum open interest

# ─────────────────────────────────────────────────────────────────────────────
# FLOW DETECTION
# ─────────────────────────────────────────────────────────────────────────────
UNUSUAL_FLOW_VOL_OI_RATIO: float = 2.5   # volume / open_interest > this → unusual
UNUSUAL_FLOW_MIN_PREMIUM: float = 50_000  # min total premium ($) to flag a sweep
FLOW_LOOKBACK_EXPIRY_DAYS: int = 45       # only consider contracts expiring ≤ 45 days

# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT SELECTION
# ─────────────────────────────────────────────────────────────────────────────
TARGET_DELTA_RANGE: tuple[float, float] = (0.30, 0.55)   # sweet-spot for directional
MIN_DTE: int = 5          # days to expiry – avoid day-of-expiry contracts
MAX_DTE: int = 45         # keep it short-dated for scalping
MIN_THETA: float = -0.80  # reject contracts bleeding > $0.80 / day per share
# Note: SPY/QQQ options naturally have theta -0.30 to -0.50 due to high underlying price.
# Theta is also used as a score component (not just a hard gate) via the greeks score.

# ─────────────────────────────────────────────────────────────────────────────
# SCORING WEIGHTS  (must sum to 1.0)
# ─────────────────────────────────────────────────────────────────────────────
SCORE_WEIGHTS: dict[str, float] = {
    "flow":       0.35,   # unusual activity signal
    "execution":  0.30,   # spread quality + liquidity
    "greeks":     0.20,   # delta/theta quality
    "iv":         0.15,   # IV rank / regime
}

# ─────────────────────────────────────────────────────────────────────────────
# TRADE FILTER
# ─────────────────────────────────────────────────────────────────────────────
MIN_COMPOSITE_SCORE: float = 60.0   # 0–100; only trade ≥ this score
MIN_FLOW_SCORE: float = 50.0        # flow alone must be at least this strong

# ─────────────────────────────────────────────────────────────────────────────
# ALERT / LOGGING
# ─────────────────────────────────────────────────────────────────────────────
LOG_DIR: str = "logs"
LOG_FILE: str = "trading_system.log"
ALERT_LEVEL: str = "INFO"   # DEBUG | INFO | WARNING
