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

## Project Chimera — Mainframe.py
Orchestrator: boots DataAgent, StrategyAgent, and RiskAgent as asyncio tasks.
The shared State Dictionary bridges all three layers.
The News Agent can veto any technical signal before it reaches the OMS.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from chimera.agents.data_agent import DataAgent
from chimera.agents.strategy_agent import StrategyAgent
from chimera.agents.risk_agent import RiskAgent
from chimera.agents.news_agent import NewsAgent
from chimera.oms.order_manager import OrderManager
from chimera.social.scraper import StocktwitsScraper
from chimera.risk.circuit_breaker import CircuitBreaker
from chimera.oms.trade_logger import TradeLogger
from chimera.server.runner import APIServer
from chimera.utils.state import SharedState
from chimera.utils.logger import setup_logger

log = setup_logger("mainframe")


class Mainframe:
    """
    Top-level orchestrator. Creates one shared state object and passes it to
    every agent. Agents communicate exclusively through state — never by calling
    each other directly. This keeps the architecture loosely coupled and makes
    individual agents testable in isolation.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.state = SharedState()
        self._tasks: list[asyncio.Task] = []

        # Instantiate agents, all sharing the same state reference
        self.data_agent     = DataAgent(self.state, config)
        self.news_agent     = NewsAgent(self.state, config)
        self.strategy_agent = StrategyAgent(self.state, config)
        self.risk_agent     = RiskAgent(self.state, config)
        self.trade_logger   = TradeLogger(config.get("db_path", "chimera_trades.db"))
        self.order_manager  = OrderManager(self.state, config)
        self.api_server       = APIServer(self.state, self.trade_logger, config)
        self.social_scraper    = StocktwitsScraper(self.state, config)
        self.circuit_breaker   = CircuitBreaker(self.state, self.order_manager, config)

    async def run(self) -> None:
        log.info("╔══════════════════════════════════╗")
        log.info("║   Project Chimera — MAINFRAME     ║")
        log.info("╚══════════════════════════════════╝")
        log.info(f"Boot time: {datetime.utcnow().isoformat()}Z")
        log.info(f"Mode: {self.config.get('mode', 'paper')}")

        self._tasks = [
            asyncio.create_task(self.data_agent.run(),     name="DataAgent"),
            asyncio.create_task(self.news_agent.run(),     name="NewsAgent"),
            asyncio.create_task(self.strategy_agent.run(), name="StrategyAgent"),
            asyncio.create_task(self.risk_agent.run(),     name="RiskAgent"),
            asyncio.create_task(self.order_manager.run(),  name="OrderManager"),
            asyncio.create_task(self.api_server.run(),     name="APIServer"),
            asyncio.create_task(self.social_scraper.run(),    name="SocialScraper"),
            asyncio.create_task(self.circuit_breaker.run(), name="CircuitBreaker"),
        ]

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            log.warning("Mainframe received shutdown signal.")
        except Exception as e:
            log.exception(f"Fatal error in mainframe: {e}")
        finally:
            await self._shutdown()

    async def _shutdown(self) -> None:
        log.info("Shutting down all agents...")
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        log.info("All agents halted. Goodbye.")


if __name__ == "__main__":
    from chimera.config.settings import load_config

    cfg = load_config()
    mainframe = Mainframe(cfg)
    asyncio.run(mainframe.run())
