"""回测框架（防前视 / 基准对照 / 样本量提示）。

严格遵守 PIT：信号在当日盘后才能计算，因此最早入场时点为**下一交易日**收盘，
绝不使用当日收盘价成交，也不使用晚于信号日的持仓披露数据。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import db
from .cleaning import assert_no_lookahead
from .signals import POLICY_BOTTOM_PROBABLE


@dataclass
class BacktestResult:
    events: int
    horizon_days: int
    win_rate: float | None
    avg_forward_return_pct: float | None
    max_drawdown_pct: float | None
    benchmark_return_pct: float | None
    benchmark_label: str
    note: str

    def as_dict(self) -> dict:
        return {
            "events": self.events,
            "horizon_days": self.horizon_days,
            "win_rate": self.win_rate,
            "avg_forward_return_pct": self.avg_forward_return_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "benchmark_return_pct": self.benchmark_return_pct,
            "benchmark_label": self.benchmark_label,
            "note": self.note,
        }


def run_backtest(horizon_days: int = 20) -> BacktestResult:
    with db.connect() as conn:
        sig_rows = conn.execute(
            "SELECT date,state FROM signals WHERE state=? ORDER BY date",
            (POLICY_BOTTOM_PROBABLE,),
        ).fetchall()
        markets = conn.execute(
            "SELECT date,index_close FROM market_daily ORDER BY date"
        ).fetchall()

    closes = [r["index_close"] for r in markets]
    dates = [r["date"] for r in markets]
    pos = {d: i for i, d in enumerate(dates)}

    # PIT 校验：所有持仓记录都必须早于或等于回测终点
    holdings = db.get_holdings(limit=10000)
    as_of = dates[-1] if dates else "9999-12-31"
    assert_no_lookahead(holdings, as_of, key="effective_date")

    returns: list[float] = []
    for s in sig_rows:
        entry = pos.get(s["date"])
        if entry is None:
            continue
        # 盘后才知道信号 → 下一交易日收盘入场
        i0 = entry + 1
        i1 = i0 + horizon_days
        if i1 >= len(closes) or i0 >= len(closes):
            continue
        returns.append((closes[i1] / closes[i0] - 1) * 100)

    note = (
        "对照组为同期指数买入持有；信号为 L2/L3 概率推断，is_confirmed=False。"
        "国家队进场事件在历史上为个位数，样本量极小，任何统计结论都极易过拟合；"
        "政策底不等于市场底（历史间隔 40 天至 6 个月）。本结果不构成投资建议。"
    )
    if not returns:
        return BacktestResult(0, horizon_days, None, None, None, None, "指数买入持有", note)

    wins = sum(1 for r in returns if r > 0)
    benchmark = (closes[-1] / closes[0] - 1) * 100 if len(closes) > 1 else 0.0
    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        peak = max(peak, c)
        max_dd = min(max_dd, (c / peak - 1) * 100)

    return BacktestResult(
        events=len(returns),
        horizon_days=horizon_days,
        win_rate=round(wins / len(returns) * 100, 2),
        avg_forward_return_pct=round(sum(returns) / len(returns), 2),
        max_drawdown_pct=round(max_dd, 2),
        benchmark_return_pct=round(benchmark, 2),
        benchmark_label="指数买入持有（同期全区间）",
        note=note,
    )
