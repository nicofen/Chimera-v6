"""
chimera/backtest/performance.py
PerformanceReport — computes all standard quantitative metrics from
a completed backtest's closed_trades list and equity_curve.

Metrics computed:
  Core:      total_return, CAGR, total_trades, win_rate, profit_factor
  R-stats:   avg_r, avg_win_r, avg_loss_r, expectancy_r
  Risk:      max_drawdown, max_drawdown_duration, calmar_ratio
  Risk-adj:  sharpe_ratio, sortino_ratio
  Streaks:   max_win_streak, max_loss_streak
  Per-sector breakdown of win_rate and avg_r
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any


class PerformanceReport:
    """
    Instantiate with the BacktestEngine's closed_trades and equity_curve.
    Call .compute() to get a flat dict of all metrics.
    Call .print_summary() for a formatted console report.
    """

    def __init__(
        self,
        trades:       list[dict[str, Any]],
        equity_curve: list[tuple[datetime, float]],
        initial_equity: float,
        config:       dict[str, Any] | None = None,
    ):
        self.trades         = trades
        self.equity_curve   = equity_curve
        self.initial_equity = initial_equity
        self.config         = config or {}

    def compute(self) -> dict[str, Any]:
        if not self.trades:
            return {"error": "No closed trades — nothing to report."}

        m: dict[str, Any] = {}

        # ── Core trade stats ──────────────────────────────────────────────
        pnls  = [t["realised_pnl"] for t in self.trades]
        rs    = [t["r_multiple"]   for t in self.trades]
        wins  = [p for p in pnls if p > 0]
        losses= [p for p in pnls if p <= 0]

        m["total_trades"]  = len(self.trades)
        m["winning_trades"]= len(wins)
        m["losing_trades"] = len(losses)
        m["win_rate"]      = round(len(wins) / len(pnls), 4)
        m["gross_profit"]  = round(sum(wins), 2)
        m["gross_loss"]    = round(abs(sum(losses)), 2)
        m["net_profit"]    = round(sum(pnls), 2)
        m["profit_factor"] = round(
            sum(wins) / abs(sum(losses)) if losses else float("inf"), 3
        )
        m["commission_total"] = round(sum(t.get("commission", 0) for t in self.trades), 2)

        # ── Return metrics ────────────────────────────────────────────────
        final_equity = self.initial_equity + m["net_profit"]
        m["initial_equity"] = round(self.initial_equity, 2)
        m["final_equity"]   = round(final_equity, 2)
        m["total_return_pct"] = round((final_equity / self.initial_equity - 1) * 100, 3)

        if self.equity_curve and len(self.equity_curve) >= 2:
            days = (self.equity_curve[-1][0] - self.equity_curve[0][0]).days
            years = max(days / 365.25, 1 / 365.25)
            m["backtest_days"] = days
            if final_equity > 0 and self.initial_equity > 0:
                m["cagr_pct"] = round(
                    ((final_equity / self.initial_equity) ** (1 / years) - 1) * 100, 3
                )
            else:
                m["cagr_pct"] = 0.0
        else:
            m["backtest_days"] = 0
            m["cagr_pct"] = 0.0

        # ── R-multiple stats ──────────────────────────────────────────────
        win_rs  = [r for r in rs if r > 0]
        loss_rs = [r for r in rs if r <= 0]

        m["avg_r"]        = round(_mean(rs), 3)
        m["avg_win_r"]    = round(_mean(win_rs),  3) if win_rs  else 0.0
        m["avg_loss_r"]   = round(_mean(loss_rs), 3) if loss_rs else 0.0
        m["expectancy_r"] = round(
            m["win_rate"] * m["avg_win_r"] + (1 - m["win_rate"]) * m["avg_loss_r"], 3
        )
        m["best_trade_r"]  = round(max(rs), 3)
        m["worst_trade_r"] = round(min(rs), 3)

        # ── Drawdown ──────────────────────────────────────────────────────
        if self.equity_curve:
            dd_pct, dd_dur = _max_drawdown(self.equity_curve)
            m["max_drawdown_pct"]      = round(dd_pct * 100, 3)
            m["max_drawdown_days"]     = dd_dur
            m["calmar_ratio"] = round(
                m["cagr_pct"] / m["max_drawdown_pct"]
                if m["max_drawdown_pct"] > 0 else 0.0, 3
            )
        else:
            m["max_drawdown_pct"]  = 0.0
            m["max_drawdown_days"] = 0
            m["calmar_ratio"]      = 0.0

        # ── Sharpe / Sortino ──────────────────────────────────────────────
        if len(pnls) >= 5:
            rfr_daily = self.config.get("risk_free_rate", 0.05) / 252
            returns   = [p / self.initial_equity for p in pnls]
            excess    = [r - rfr_daily for r in returns]
            m["sharpe_ratio"]  = round(_sharpe(excess), 3)
            m["sortino_ratio"] = round(_sortino(returns, rfr_daily), 3)
        else:
            m["sharpe_ratio"]  = 0.0
            m["sortino_ratio"] = 0.0

        # ── Streak analysis ───────────────────────────────────────────────
        m["max_win_streak"]  = _max_streak(pnls, positive=True)
        m["max_loss_streak"] = _max_streak(pnls, positive=False)

        # ── Per-sector breakdown ──────────────────────────────────────────
        by_sector: dict[str, list] = defaultdict(list)
        for t in self.trades:
            by_sector[t.get("sector", "unknown")].append(t)

        sector_stats = {}
        for sec, sec_trades in by_sector.items():
            sec_pnls = [t["realised_pnl"] for t in sec_trades]
            sec_rs   = [t["r_multiple"]   for t in sec_trades]
            sec_wins = sum(1 for p in sec_pnls if p > 0)
            sector_stats[sec] = {
                "trades":   len(sec_trades),
                "win_rate": round(sec_wins / len(sec_trades), 3),
                "avg_r":    round(_mean(sec_rs), 3),
                "net_pnl":  round(sum(sec_pnls), 2),
            }
        m["by_sector"] = sector_stats

        # ── Close reason breakdown ────────────────────────────────────────
        by_reason: dict[str, int] = defaultdict(int)
        for t in self.trades:
            by_reason[t.get("close_reason", "unknown")] += 1
        m["close_reasons"] = dict(by_reason)

        return m

    def print_summary(self) -> None:
        m = self.compute()
        if "error" in m:
            print(m["error"])
            return

        w = 52
        sep = "─" * w

        def row(label: str, val: Any, width: int = 30) -> str:
            return f"  {label:<{width}} {val}"

        print(f"\n╔{'═' * w}╗")
        print(f"║{'PROJECT CHIMERA — BACKTEST REPORT':^{w}}║")
        print(f"╚{'═' * w}╝")
        print(f"\n{sep}")
        print("  OVERVIEW")
        print(sep)
        print(row("Backtest period:", f"{m['backtest_days']} days"))
        print(row("Initial equity:",  f"${m['initial_equity']:,.2f}"))
        print(row("Final equity:",    f"${m['final_equity']:,.2f}"))
        print(row("Net profit:",      f"${m['net_profit']:,.2f}"))
        print(row("Total return:",    f"{m['total_return_pct']:.2f}%"))
        print(row("CAGR:",            f"{m['cagr_pct']:.2f}%"))
        print(row("Commissions:",     f"${m['commission_total']:,.2f}"))

        print(f"\n{sep}")
        print("  TRADE STATISTICS")
        print(sep)
        print(row("Total trades:",    m['total_trades']))
        print(row("Win rate:",        f"{m['win_rate']*100:.1f}%  ({m['winning_trades']}W / {m['losing_trades']}L)"))
        print(row("Profit factor:",   f"{m['profit_factor']:.3f}"))
        print(row("Avg R:",           f"{m['avg_r']:+.3f}R"))
        print(row("Avg win R:",       f"{m['avg_win_r']:+.3f}R"))
        print(row("Avg loss R:",      f"{m['avg_loss_r']:+.3f}R"))
        print(row("Expectancy:",      f"{m['expectancy_r']:+.3f}R per trade"))
        print(row("Best trade:",      f"{m['best_trade_r']:+.3f}R"))
        print(row("Worst trade:",     f"{m['worst_trade_r']:+.3f}R"))
        print(row("Max win streak:",  m['max_win_streak']))
        print(row("Max loss streak:", m['max_loss_streak']))

        print(f"\n{sep}")
        print("  RISK METRICS")
        print(sep)
        print(row("Max drawdown:",    f"{m['max_drawdown_pct']:.2f}%  ({m['max_drawdown_days']} days)"))
        print(row("Sharpe ratio:",    f"{m['sharpe_ratio']:.3f}"))
        print(row("Sortino ratio:",   f"{m['sortino_ratio']:.3f}"))
        print(row("Calmar ratio:",    f"{m['calmar_ratio']:.3f}"))

        print(f"\n{sep}")
        print("  BY SECTOR")
        print(sep)
        for sec, stats in m["by_sector"].items():
            print(row(f"  {sec.upper()}:",
                      f"{stats['trades']} trades  "
                      f"WR={stats['win_rate']*100:.0f}%  "
                      f"avgR={stats['avg_r']:+.2f}  "
                      f"P&L=${stats['net_pnl']:,.0f}"))

        print(f"\n{sep}")
        print("  EXIT REASONS")
        print(sep)
        for reason, count in m["close_reasons"].items():
            print(row(f"  {reason}:", count))
        print()


# ── Statistical helpers ────────────────────────────────────────────────────────

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _sharpe(excess_returns: list[float]) -> float:
    if not excess_returns:
        return 0.0
    mu  = _mean(excess_returns)
    std = _std(excess_returns)
    if std == 0:
        return 0.0
    return mu / std * math.sqrt(252)


def _sortino(returns: list[float], rfr: float) -> float:
    if not returns:
        return 0.0
    mu       = _mean(returns) - rfr
    downside = [min(r - rfr, 0) for r in returns]
    ds_std   = math.sqrt(sum(d ** 2 for d in downside) / max(len(downside) - 1, 1))
    if ds_std == 0:
        return 0.0
    return mu / ds_std * math.sqrt(252)


def _max_drawdown(
    equity_curve: list[tuple[datetime, float]]
) -> tuple[float, int]:
    """Returns (max_drawdown_fraction, max_drawdown_duration_days)."""
    peak         = equity_curve[0][1]
    peak_dt      = equity_curve[0][0]
    max_dd       = 0.0
    max_dd_days  = 0

    for dt, eq in equity_curve[1:]:
        if eq > peak:
            peak    = eq
            peak_dt = dt
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd      = dd
            max_dd_days = (dt - peak_dt).days

    return max_dd, max_dd_days


def _max_streak(pnls: list[float], positive: bool) -> int:
    max_s = cur_s = 0
    for p in pnls:
        if (positive and p > 0) or (not positive and p <= 0):
            cur_s += 1
            max_s  = max(max_s, cur_s)
        else:
            cur_s = 0
    return max_s
