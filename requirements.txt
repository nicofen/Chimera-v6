# Project Chimera

Institutional-grade, multi-asset trading mainframe built on an async
Producer-Consumer microservices architecture.

## Architecture

```
Ingestor Layer   →  DataAgent (WebSocket + REST)
                    NewsAgent (LLM NLP + Veto)
         ↓
Processor Layer  →  StrategyAgent (TA Engine + Sp Score)
                    RiskAgent (Kelly + ATR Stops)
         ↓
Executor Layer   →  OMS (Alpaca REST + Trade Logger)
```

## Quick Start

```bash
# 1. Create a virtual environment
python -m venv .venv && source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your .env file (never commit this)
cat > .env << EOF
ALPACA_KEY=your_alpaca_key
ALPACA_SECRET=your_alpaca_secret
OPENAI_API_KEY=your_openai_key
WHALE_ALERT_KEY=your_whale_alert_key   # optional
DUNE_API_KEY=your_dune_key             # optional
CHIMERA_MODE=paper                     # ALWAYS start with paper
EOF

# 4. Run the mainframe
python -m chimera.mainframe
```

## The Four Pillars

| Sector  | Edge                         | Key Data Sources              |
|---------|------------------------------|-------------------------------|
| Crypto  | Exchange inflow/outflow      | Whale Alert, Dune, Alpaca WS  |
| Stocks  | Short Squeeze (Sp score)     | Finviz, Stocktwits, Alpaca    |
| Forex   | NLP momentum on EMA          | FinancialJuice, Alpaca        |
| Futures | Value Area mean reversion    | Alpaca CME, AVWAP             |

## Squeeze Probability Score

```
Sp = (SI × 0.4) + (V_velocity × 0.3) + (S_sentiment × 0.3)
```

Where:
- `SI`          = Normalised short interest (0–1, cap at 50%)
- `V_velocity`  = Normalised relative volume (RVOL 1–10 → 0–1)
- `S_sentiment` = Normalised Z-score of social mentions (0–5 → 0–1)

Signals with Sp < 0.60 are discarded. Sp > 0.75 triggers a long.

## Position Sizing

```
Position Size = (Account Equity × Risk%) / (ATR × 2)
```

Risk% is bounded by:
1. `base_risk_pct` from config (default 1%)
2. Kelly Criterion (computed from rolling 50-trade win history)
3. News Agent confidence multiplier (0 during veto)

## The Veto System

The News Agent raises `veto_active = True` when any of the following are
detected in FinancialJuice or Stocktwits headlines:
- FOMC / Fed meeting / rate decision
- CPI / PCE / NFP releases
- Emergency central bank actions

All pending signals are **dropped** and the system stays in cash for a
configurable cool-down window (default: 10 minutes).

## Important Warning

Past performance of any trading strategy is not indicative of future results.
Always paper-trade for a minimum of 3 months before risking real capital.
Never risk more than you can afford to lose entirely.

## Backtesting

```bash
# Stocks — 2 years of daily bars
python -m chimera.backtest.run_backtest \
    --sector stocks \
    --symbols GME AMC TSLA \
    --start 2022-01-01 --end 2024-01-01 \
    --equity 100000

# Crypto — 6 months of 4-hour bars
python -m chimera.backtest.run_backtest \
    --sector crypto \
    --symbols BTC/USD ETH/USD \
    --start 2023-06-01 --end 2024-01-01 \
    --timeframe 4Hour

# From local CSV (no Alpaca key needed)
python -m chimera.backtest.run_backtest \
    --csv ./data/GME_daily.csv --symbol GME --sector stocks \
    --start 2023-01-01 --end 2024-01-01

# Python API
from chimera.backtest import BacktestEngine
from chimera.config.settings import load_config

engine = BacktestEngine(load_config())
report = engine.run(
    symbols={"stocks": ["GME", "AMC"], "crypto": ["BTC/USD"]},
    start="2022-01-01", end="2024-01-01",
)
report.print_summary()
metrics = report.compute()   # returns a flat dict
```

### Output sample
```
╔════════════════════════════════════════════════════╗
║         PROJECT CHIMERA — BACKTEST REPORT          ║
╚════════════════════════════════════════════════════╝

────────────────────────────────────────────────────
  OVERVIEW
────────────────────────────────────────────────────
  Backtest period:               730 days
  Initial equity:                $100,000.00
  Final equity:                  $128,441.20
  Net profit:                    $28,441.20
  Total return:                  28.441%
  CAGR:                          13.22%

  TRADE STATISTICS
────────────────────────────────────────────────────
  Win rate:                      63.2%  (48W / 28L)
  Profit factor:                 1.847
  Avg R:                         +0.412R
  Expectancy:                    +0.418R per trade
```

### Architecture note
The backtester reuses `StrategyAgent` and `RiskAgent` verbatim — the same
code that runs in live trading. Only the OMS is replaced with `SimulatedOMS`
which fills against bar OHLCV data. This ensures backtest results are
directly comparable to live paper-trading results.

Data is cached locally as Parquet after the first Alpaca fetch —
re-running the same date range is instant.

## Social velocity (Stocktwits Z-score)

The `social_zscore` field in each stock's market data feeds directly into
the Sp squeeze-probability formula. It is computed by the `StocktwitsScraper`
agent which runs as a mainframe task alongside the other agents.

### How it works

1. **Scraper** polls `api.stocktwits.com/api/2/streams/symbol/{SYM}` every
   60 seconds for each stock symbol, rate-limited to 180 req/hr.
2. **MentionWindow** keeps a timestamped rolling deque of all mentions per
   symbol. Old mentions expire automatically after 24 hours.
3. **ZScoreEngine** takes a baseline snapshot of the 5-minute mention count
   every 10 minutes. After 6+ snapshots it computes:
   ```
   Z = (current_5m_count - μ_baseline) / σ_baseline
   ```
4. **SentimentTagger** classifies each message as bullish/bearish/neutral
   using the Stocktwits API label (when provided by the poster) or a
   weighted keyword lexicon as fallback.
5. Z-score is written to `state.market.stocks[sym]["social_zscore"]` where
   `StrategyAgent` picks it up for the Sp score formula.

### Standalone monitor

```bash
# Watch live Z-scores for your watchlist
python -m chimera.social.monitor --symbols GME AMC TSLA BBBY NVDA

# Custom poll interval
python -m chimera.social.monitor --symbols GME --interval 30
```

Output:
```
  SYMBOL    5m     1h   Z-SCORE  SPIKE  SENTIMENT   CI
  ──────────────────────────────────────────────────────
  GME        12     48    +3.42     🔥  BULLISH     0.72
  AMC         3     21    +0.81      -  NEUTRAL     0.41
  TSLA        7     31    +1.23      -  BEARISH     0.58
```

A Z-score ≥ 2.0 triggers the spike flag and contributes the maximum
`S_sentiment` term (1.0) to the Sp formula. Below 1.0, the contribution
scales linearly.

## Circuit Breaker

Three independent trip conditions protect capital at all times:

| Condition | Default | Auto-reset |
|-----------|---------|------------|
| Daily loss | 5% of start-of-day equity | Midnight UTC |
| Peak-to-trough drawdown | 10% from high-water mark | Manual only |
| Consecutive loss streak | 4 losses in a row | On first win or manual reset |

When any condition fires:
1. `state.circuit_open = True` — `StrategyAgent` and `RiskAgent` block all output
2. `OMS.force_close_all()` — every open position liquidated at market
3. Event persisted to `breaker_events` SQLite table
4. Dashboard shows full-width red banner with trip reason

**Manual reset** (required after drawdown or streak trips):
```bash
# Via REST API
curl -X POST "http://localhost:8765/api/breaker/reset?note=investigated+ok"

# Via Python
from chimera.risk import CircuitBreaker
breaker.reset("investigated, root cause identified")
```

**Configuration** (`.env`):
```
CB_DAILY_LOSS_PCT=0.05     # 5% daily loss limit
CB_DRAWDOWN_PCT=0.10       # 10% drawdown halt
CB_STREAK_LIMIT=4          # 4 consecutive losses
```

**REST endpoints**:
- `GET  /api/breaker`        — current status
- `POST /api/breaker/reset`  — re-arm after investigation
- `POST /api/breaker/trip`   — emergency flatten (manual trigger)
