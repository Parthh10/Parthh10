"""
alerts/alert_system.py – Alert generation and logging.

Produces three types of output:
  1. Console alerts  – colour-coded terminal output
  2. Log file        – structured log in logs/trading_system.log
  3. Alert objects   – dicts for downstream use (webhooks, Telegram, etc.)

Alert levels:
  SWEEP   – detected unusual sweep ($50k+ premium, high vol/OI)
  SIGNAL  – composite score >= threshold (trade opportunity)
  WATCH   – approaching threshold (monitoring)
  CAUTION – execution quality warning
"""

import logging
import logging.handlers
import os
from datetime import datetime
from typing import Optional

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

# ─────────────────────────────────────────────────────────────────────────────
# Setup module-level logger
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging() -> logging.Logger:
    """Configure and return the root logger for the trading system."""
    os.makedirs(config.LOG_DIR, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, config.ALERT_LEVEL, logging.INFO))

    # File handler (rotating, max 5 MB × 3 backups)
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(config.LOG_DIR, config.LOG_FILE),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(logging.WARNING)   # only warnings + errors to console from lib code
    root.addHandler(ch)

    return root


_alert_logger = logging.getLogger("alerts")


# ─────────────────────────────────────────────────────────────────────────────
# Alert constructors
# ─────────────────────────────────────────────────────────────────────────────

def _make_alert(
    level: str,
    ticker: str,
    message: str,
    extra: Optional[dict] = None,
) -> dict:
    alert = {
        "timestamp": datetime.utcnow().isoformat(),
        "level": level,
        "ticker": ticker,
        "message": message,
    }
    if extra:
        alert.update(extra)
    return alert


def _console_print(level: str, ticker: str, message: str) -> None:
    """Print a formatted alert to the console."""
    prefix = {
        "SWEEP":   "🚨 SWEEP  ",
        "SIGNAL":  "✅ SIGNAL ",
        "WATCH":   "👀 WATCH  ",
        "CAUTION": "⚠️  CAUTION",
        "INFO":    "ℹ️  INFO   ",
    }.get(level, f"   {level}  ")
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"  [{ts}] {prefix}  {ticker:6s}  {message}")


# ─────────────────────────────────────────────────────────────────────────────
# Public alert functions
# ─────────────────────────────────────────────────────────────────────────────

def sweep_alert(
    ticker: str,
    contract_symbol: str,
    direction: str,
    total_premium: float,
    flow_score: float,
) -> dict:
    msg = (
        f"{direction.upper()} sweep detected  "
        f"contract={contract_symbol}  "
        f"premium=${total_premium:,.0f}  "
        f"flow_score={flow_score:.0f}"
    )
    _alert_logger.warning("SWEEP  %s  %s", ticker, msg)
    _console_print("SWEEP", ticker, msg)
    return _make_alert("SWEEP", ticker, msg, {
        "contract": contract_symbol,
        "direction": direction,
        "total_premium": total_premium,
        "flow_score": flow_score,
    })


def signal_alert(
    ticker: str,
    verdict: str,
    composite_score: float,
    best_contract: Optional[dict] = None,
    size_suggestion: str = "",
) -> dict:
    contract_info = ""
    if best_contract:
        contract_info = (
            f"  contract={best_contract.get('contractSymbol', '?')}  "
            f"strike={best_contract.get('strike', '?')}  "
            f"expiry={best_contract.get('expiry', '?')}  "
            f"mid=${best_contract.get('mid', 0):.2f}"
        )
    msg = f"[{verdict}] score={composite_score:.1f}{contract_info}  size={size_suggestion}"
    _alert_logger.info("SIGNAL  %s  %s", ticker, msg)
    _console_print("SIGNAL", ticker, msg)
    return _make_alert("SIGNAL", ticker, msg, {
        "verdict": verdict,
        "composite_score": composite_score,
        "size_suggestion": size_suggestion,
    })


def watch_alert(ticker: str, composite_score: float, reason: str) -> dict:
    msg = f"score={composite_score:.1f}  reason={reason}"
    _alert_logger.info("WATCH  %s  %s", ticker, msg)
    _console_print("WATCH", ticker, msg)
    return _make_alert("WATCH", ticker, msg, {"composite_score": composite_score})


def caution_alert(ticker: str, reason: str) -> dict:
    msg = f"reason={reason}"
    _alert_logger.warning("CAUTION  %s  %s", ticker, msg)
    _console_print("CAUTION", ticker, msg)
    return _make_alert("CAUTION", ticker, msg)


def info_alert(ticker: str, message: str) -> dict:
    _alert_logger.info("INFO  %s  %s", ticker, message)
    _console_print("INFO", ticker, message)
    return _make_alert("INFO", ticker, message)
