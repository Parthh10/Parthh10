# Free-Tier Options Trading System

A **fully programmatic** options scanning and trade-selection engine built
entirely on **free data sources** — no Polygon.io, no Bloomberg, no paid
subscriptions required.

---

## What This Solves

| Gap identified | How this system fixes it (free) |
|---|---|
| Real-time options Greeks | **Tradier free dev API** (delta/gamma/theta/vega/rho via ORATS) + Black-Scholes fallback |
| IV surface | Computed from options chain + 52-week historical vol |
| IV Rank & Percentile | Calculated from Yahoo Finance 1-year history |
| Bid/ask spread quality | `spread_analyzer.py` — scores every contract 0-100, flags untradeable spreads |
| Execution quality | `execution_filter.py` — answers: *will this fill cleanly? are you overpaying?* |
| Full options chain depth | yfinance (all expirations, all strikes) + Tradier full chain |
| Unusual flow / sweeps | `flow_detector.py` — volume/OI ratio + premium size + DTE urgency |
| Scoring / filtering engine | `scoring_engine.py` — 0-100 composite score across 4 dimensions |
| Contract selection | `contract_selector.py` — optimal strike × expiry × delta selection |
| Trade watchlist | `trade_filter.py` — final ranked, actionable list with position sizing |

---

## Architecture

```
trading_system/
├── main.py                    ← Orchestrator (run this)
├── config.py                  ← All thresholds, weights, API keys
├── requirements.txt
├── .env.example               ← Copy → .env and fill in keys
│
├── data/
│   ├── yfinance_fetcher.py    ← Yahoo Finance (FREE, no key, 15-min delay)
│   └── tradier_fetcher.py     ← Tradier API  (FREE with account, real-time)
│
├── analytics/
│   ├── greeks.py              ← Black-Scholes: Δ Γ Θ V ρ + IV solver
│   ├── iv_calculator.py       ← IV Rank, IV Percentile, IV Surface, IV Regime
│   └── spread_analyzer.py     ← Bid/ask spread scoring + fill quality estimate
│
├── engine/
│   ├── flow_detector.py       ← Unusual flow: Vol/OI ratio, sweep detection
│   ├── contract_selector.py   ← Best strike + expiry selection
│   ├── execution_filter.py    ← Execution quality gate (spread + IV check)
│   ├── scoring_engine.py      ← Master 0-100 composite score
│   └── trade_filter.py        ← Final watchlist + position sizing
│
└── alerts/
    └── alert_system.py        ← Console + log alerts (sweeps, signals, cautions)
```

---

## Quick Start

### 1. Install dependencies

```bash
cd trading_system
pip install -r requirements.txt
```

### 2. (Optional but recommended) Get a free Tradier API key

1. Go to <https://developer.tradier.com/>
2. Click **Get an API Key** (free registration; no credit card typically required)
3. Copy your **sandbox token**
4. Create your `.env` file:

```bash
cp .env.example .env
# Edit .env and paste your token:
# TRADIER_TOKEN=your_sandbox_token_here
```

> **Without a Tradier key:** The system falls back to Yahoo Finance automatically.
> You get full functionality with a ~15-minute data delay. Perfect for learning
> and end-of-day scanning.

### 3. Run the scanner

```bash
# Scan the default 10-ticker universe
python main.py

# Scan specific tickers
python main.py --tickers SPY QQQ NVDA TSLA
```

---

## Score Interpretation

| Score | Verdict | Action |
|---|---|---|
| 80 – 100 | **STRONG BUY** | 75–100% of max position |
| 60 – 79 | **BUY** | 25–75% of max position |
| 45 – 59 | **WATCH** | Monitor; wait for confirmation |
| 0 – 44 | **SKIP** | Do not trade |

---

## Scoring Breakdown (0–100)

```
Composite = 35% Flow + 30% Execution + 20% Greeks + 15% IV
```

| Dimension | What it measures | Key signals |
|---|---|---|
| **Flow (35%)** | Unusual smart-money activity | Vol/OI ratio, sweep premium, DTE urgency |
| **Execution (30%)** | Can you fill this profitably? | Spread %, bid/ask quality, liquidity |
| **Greeks (20%)** | Is the contract structurally sound? | Delta in target range, theta not excessive |
| **IV (15%)** | Are you buying cheap or expensive premium? | IV Rank, IV regime |

### Hard gates (override composite score)
- Execution **rejected** → composite capped at 30 (untradeable spread)
- Flow score < 50 → composite capped at 50 (no smart-money signal)

---

## Free Data Sources

### Yahoo Finance (yfinance) — zero cost, no signup
- Options chain: all strikes × all expirations
- Bid, ask, volume, open interest, implied volatility
- 15-minute delayed quotes
- 1-year historical OHLCV (for IV rank)

### Tradier Developer API — free with registration
- **Sign up:** <https://developer.tradier.com/>
- Full real-time options chain with ORATS Greeks
- Delta, Gamma, Theta, Vega, Rho per contract
- Real-time underlying quotes
- WebSocket streaming (for live scanning)

### Black-Scholes (built-in, no API)
- Computed Greeks when API Greeks are unavailable
- IV solver (Brent's method) from market prices
- IV surface from chain data
- Historical volatility from price history

---

## Configuration (`config.py`)

Key parameters you can tune:

```python
# Tickers to scan
DEFAULT_TICKERS = ["SPY", "QQQ", "AAPL", ...]

# Execution filters
MAX_SPREAD_PCT = 0.10     # reject if spread > 10% of mid
MAX_SPREAD_ABS = 0.50     # reject if spread > $0.50
MIN_OPTION_VOLUME = 50    # minimum daily volume
MIN_OPEN_INTEREST = 200   # minimum open interest

# Contract selection
TARGET_DELTA_RANGE = (0.30, 0.55)  # near-ATM directional
MIN_DTE = 5
MAX_DTE = 45

# Score thresholds
MIN_COMPOSITE_SCORE = 60.0
MIN_FLOW_SCORE = 50.0

# Scoring weights
SCORE_WEIGHTS = {
    "flow": 0.35, "execution": 0.30, "greeks": 0.20, "iv": 0.15
}
```

---

## Execution Intelligence (The Scalper's Edge)

This system replicates the execution analysis that hedge funds run before
every trade:

```
Signal is correct + Flow is correct BUT spread is $0.40 wide → You LOSE money anyway
```

The `execution_filter.py` answers:

1. **Is the spread too wide?**
   → Rejects if `spread > MAX_SPREAD_ABS` or `spread_pct > MAX_SPREAD_PCT`

2. **Will this order fill cleanly?**
   → Estimates fill price between mid and ask based on spread width

3. **Are you overpaying premium?**
   → Compares contract IV to HV30 (historical vol). If IV/HV > 1.5×, warns you.

---

## Example Output

```
══════════════════════════════════════════════════════════════════════════════════════════
  TRADING WATCHLIST
══════════════════════════════════════════════════════════════════════════════════════════
╭──────────┬─────────────┬───────┬───────────┬────────┬────────────┬─────┬──────╮
│ ticker   │ verdict     │ score │ direction │ strike │ expiry     │ dte │  mid │
├──────────┼─────────────┼───────┼───────────┼────────┼────────────┼─────┼──────┤
│ NVDA     │ STRONG BUY  │  83.2 │ bullish   │  850.0 │ 2024-04-26 │  21 │ 8.40 │
│ SPY      │ BUY         │  71.5 │ bullish   │  510.0 │ 2024-04-26 │  21 │ 2.85 │
│ AAPL     │ WATCH       │  52.0 │ bearish   │  175.0 │ 2024-05-03 │  28 │ 3.10 │
╰──────────┴─────────────┴───────┴───────────┴────────┴────────────┴─────┴──────╯
══════════════════════════════════════════════════════════════════════════════════════════

  2 actionable trade(s) found.
```

---

## Limitations & Honest Notes

| Limitation | Impact | Workaround |
|---|---|---|
| Yahoo Finance 15-min delay | Spreads and prices are stale for scalping | Use Tradier free token for real-time |
| No live order book depth | Can't see Level 2 | Check broker directly before entry |
| Yahoo IV can be inaccurate | Greeks may be off | System recomputes via Black-Scholes |
| Flow detection is volume-based | No actual sweep/block trade identification | Tradier + CBOE data would improve this |
| No tick-level data | Can't verify sweep timing | Upgrade path: Polygon.io ($29/mo) |

---

## Upgrade Path (when budget allows)

| Add | Cost | What it unlocks |
|---|---|---|
| Tradier brokerage account | Free | Real-time Greeks, WebSocket streaming |
| Polygon.io Starter | $29/mo | Full tick data, real sweep detection, L2 |
| Unusual Whales | $50/mo | Pre-computed flow with sentiment scoring |

The system is **designed to plug in Polygon or Unusual Whales data** — just
swap the data fetcher in `main.py` and the rest of the pipeline works unchanged.

---

## Security

- API keys are loaded from `.env` — never hard-coded
- `.env` is excluded from git (add to `.gitignore`)
- No data is sent to third parties
