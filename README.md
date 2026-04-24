"""
Project Chimera — Mainframe.py
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
