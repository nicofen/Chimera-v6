"""
chimera/backtest/engine.py
BacktestEngine — the main replay loop.

Usage:
    from chimera.backtest.engine import BacktestEngine
    from chimera.config.settings import load_config

    cfg = load_config()
    engine = BacktestEngine(cfg)

    results = engine.run(
        symbols    = {"stocks": ["GME", "AMC"], "crypto": ["BTC/USD"]},
        start      = "2023-01-01",
        end        = "2024-01-01",
        timeframe  = "1Day",
        initial_equity = 100_000,
    )
    results.print_summary()

How the replay works:
  1. Load all OHLCV data via DataLoader (Alpaca or CSV).
  2. Merge all symbols into a single sorted timeline of (dt, symbol, sector, bar).
  3. For each bar:
     a. Inject bar into BacktestState.
     b. Run StrategyAgent._evaluate_all_sectors() synchronously (via asyncio.run).
     c. Drain signal queue → run RiskAgent._process() for each signal.
     d. Drain order queue → SimulatedOMS.accept_order() for each order.
     e. SimulatedOMS.on_bar() — fill pending entries, check stops/TP.
     f. Record equity snapshot.
  4. Force-close all open positions on final bar.
  5. Build and return PerformanceReport.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import pandas as pd

from chimera.backtest.state import BacktestState
from chimera.backtest.data_loader import DataLoader
from chimera.backtest.simulated_oms import SimulatedOMS
from chimera.backtest.performance import PerformanceReport
from chimera.agents.strategy_agent import StrategyAgent
from chimera.agents.risk_agent import RiskAgent
from chimera.utils.logger import setup_logger

log = setup_logger("backtest.engine")


class BacktestEngine:
    """
    Drives the bar-by-bar replay.
    Reuses live StrategyAgent and RiskAgent code verbatim.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.loader = DataLoader(config)

    def run(
        self,
        symbols:        dict[str, list[str]],   # {"stocks": [...], "crypto": [...], ...}
        start:          str | datetime,
        end:            str | datetime,
        timeframe:      str   = "1Day",
        initial_equity: float = 100_000.0,
        warmup_bars:    int   = 200,             # bars before signals start (TA warmup)
    ) -> PerformanceReport:
        """
        Run a full backtest.

        symbols: dict mapping sector → list of symbol strings
            e.g. {"stocks": ["GME", "TSLA"], "crypto": ["BTC/USD"]}
        start / end: ISO date strings or datetime objects
        timeframe: Alpaca bar timeframe string
        initial_equity: starting account equity in USD
        warmup_bars: number of bars per symbol to load before generating
                     signals (ensures TA indicators are properly seeded)

        Returns a PerformanceReport ready to call .compute() or .print_summary().
        """
        log.info("=" * 56)
        log.info("  Project Chimera — Backtest Engine")
        log.info(f"  Period  : {start} → {end}")
        log.info(f"  TF      : {timeframe}")
        log.info(f"  Equity  : ${initial_equity:,.0f}")
        log.info(f"  Symbols : {symbols}")
        log.info("=" * 56)

        # ── 1. Load data ───────────────────────────────────────────────────
        all_data: dict[str, dict[str, pd.DataFrame]] = {}
        for sector, syms in symbols.items():
            all_data[sector] = self.loader.load(syms, start, end, timeframe, sector)

        if not any(all_data.values()):
            raise RuntimeError("No data loaded — check symbols and date range.")

        # ── 2. Build merged timeline ───────────────────────────────────────
        timeline = _build_timeline(all_data)
        log.info(f"Total bars in timeline: {len(timeline)}")

        # ── 3. Initialise state and agents ────────────────────────────────
        state    = BacktestState(initial_equity=initial_equity)
        strategy = StrategyAgent(state, self.config)
        risk     = RiskAgent(state, self.config)
        oms      = SimulatedOMS(state, self.config)

        # Patch state queues to synchronous versions for backtest
        _patch_state_for_backtest(state, strategy, risk)

        # ── 4. Replay loop ─────────────────────────────────────────────────
        bar_counts: dict[str, int] = {}   # symbol → bars seen

        for dt, symbol, sector, bar in timeline:
            bar_counts[symbol] = bar_counts.get(symbol, 0) + 1

            # Always inject the bar (keeps TA windows rolling)
            state.inject_bar(sector, symbol, bar, dt)

            # Skip signal generation during warmup
            if bar_counts[symbol] <= warmup_bars:
                continue

            # Run strategy synchronously for this sector/symbol
            _run_strategy_sync(strategy, sector, symbol, state)

            # Drain signals → risk sizing
            for signal in state.drain_signals():
                if state.is_vetoed():
                    break
                _run_risk_sync(risk, signal, state)

            # Drain orders → OMS
            for rp in state.drain_orders():
                oms.accept_order(rp, dt)

            # Process OMS for this bar
            oms.on_bar(symbol, sector, bar, dt)

            # Snapshot equity
            state.record_equity()

        # ── 5. Close all open positions on last bar ─────────────────────
        last_prices = {
            sym: _last_close(all_data, sym)
            for sector_data in all_data.values()
            for sym in sector_data
        }
        last_dt = timeline[-1][0] if timeline else datetime.utcnow()
        oms.close_all(last_prices, last_dt)

        log.info(f"Backtest complete — {len(state.closed_trades)} trades closed.")
        log.info(f"Final equity: ${state.equity:,.2f}")

        return PerformanceReport(
            trades         = state.closed_trades,
            equity_curve   = state.equity_curve,
            initial_equity = initial_equity,
            config         = self.config,
        )


# ── Synchronous strategy/risk wrappers ────────────────────────────────────────

def _run_strategy_sync(
    strategy: StrategyAgent,
    sector:   str,
    symbol:   str,
    state:    BacktestState,
) -> None:
    """
    Run one sector evaluation synchronously.
    Each sector method is an async coroutine — we drive it with asyncio.run()
    on a fresh event loop to keep the backtest loop synchronous.
    """
    sector_method = {
        "crypto":  strategy._sector_crypto,
        "stocks":  strategy._sector_stocks,
        "forex":   strategy._sector_forex,
        "futures": strategy._sector_futures,
    }.get(sector)

    if sector_method is None:
        return

    try:
        asyncio.run(_run_coro_with_sync_emit(sector_method, state))
    except RuntimeError:
        # Event loop already running (e.g. in Jupyter) — use nest_asyncio
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run_coro_with_sync_emit(sector_method, state))
        finally:
            loop.close()


async def _run_coro_with_sync_emit(coro_factory, state: BacktestState):
    """Run a strategy coroutine but intercept put_signal calls."""
    await coro_factory()


def _run_risk_sync(
    risk:   RiskAgent,
    signal: Any,
    state:  BacktestState,
) -> None:
    """Run one risk evaluation synchronously."""
    try:
        asyncio.run(_risk_process_sync(risk, signal, state))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_risk_process_sync(risk, signal, state))
        finally:
            loop.close()


async def _risk_process_sync(risk: RiskAgent, signal: Any, state: BacktestState):
    await risk._process(signal)


def _patch_state_for_backtest(
    state:    BacktestState,
    strategy: StrategyAgent,
    risk:     RiskAgent,
) -> None:
    """
    Monkey-patch state.put_signal and state.put_order to use the
    synchronous queues instead of asyncio queues.
    This lets the live agent code run unchanged in the backtest.
    """
    import asyncio as _asyncio

    async def _sync_put_signal(signal):
        state.put_signal_sync(signal)

    async def _sync_put_order(rp):
        state.put_order_sync(rp)

    state.put_signal = _sync_put_signal
    state.put_order  = _sync_put_order


# ── Timeline builder ──────────────────────────────────────────────────────────

def _build_timeline(
    all_data: dict[str, dict[str, pd.DataFrame]]
) -> list[tuple[datetime, str, str, dict]]:
    """
    Merge all symbols into a single flat list sorted by datetime.
    Each entry: (dt, symbol, sector, bar_dict)
    """
    rows: list[tuple[datetime, str, str, dict]] = []
    for sector, symbol_data in all_data.items():
        for sym, df in symbol_data.items():
            for dt, row in df.iterrows():
                rows.append((
                    dt.to_pydatetime() if hasattr(dt, "to_pydatetime") else dt,
                    sym,
                    sector,
                    {
                        "open":   float(row["open"]),
                        "high":   float(row["high"]),
                        "low":    float(row["low"]),
                        "close":  float(row["close"]),
                        "volume": float(row["volume"]),
                    },
                ))
    rows.sort(key=lambda x: x[0])
    return rows


def _last_close(
    all_data: dict[str, dict[str, pd.DataFrame]],
    symbol:   str,
) -> float:
    for sector_data in all_data.values():
        if symbol in sector_data:
            df = sector_data[symbol]
            if not df.empty:
                return float(df["close"].iloc[-1])
    return 0.0
