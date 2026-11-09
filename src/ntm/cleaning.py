"""数据清洗硬规则。

对应研判报告 P0-5 与《每日监控清单》第六节四个陷阱：
1. 十大股东名单「新进/退出」≠「增持/减持」；
2. 持股变动须按送转股/增发/回购做复权，剔除被动变动；
3. 十大股东与十大流通股东口径分开；
4. PIT：用披露日做时点，回测不得前视。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from typing import Iterable, TypeVar

EPS = 1e-6

LIST_TOP10 = "十大股东"
LIST_TOP10_FLOAT = "十大流通股东"

# 变动分类
LIST_ENTRY = "名单新进（不可判定为增持）"
LIST_EXIT = "名单退出（不可判定为减持）"
INCREASE = "主动增持"
DECREASE = "主动减持"
PASSIVE_ONLY = "被动变动（复权后无主动增减）"
UNCHANGED = "不变"
UNKNOWN = "数据不足，无法判定"


@dataclass(frozen=True)
class Snapshot:
    """某一报告期的持股快照。shares 为 None 表示未出现在该期名单中。"""
    shares: float | None
    list_type: str = LIST_TOP10_FLOAT
    rank: int | None = None


@dataclass(frozen=True)
class ChangeResult:
    classification: str
    delta_shares: float | None
    note: str
    is_actionable: bool  # 是否可作为「真实增减持」进入信号/统计


def classify_change(
    prev: Snapshot,
    curr: Snapshot,
    corporate_action_factor: float = 1.0,
) -> ChangeResult:
    """判定两期持股快照之间的变化性质。

    corporate_action_factor: 复权因子。adjusted_prev = prev.shares * factor。
    例如 10 转 3 → factor=1.3；若 curr.shares == prev.shares*1.3，则属于纯被动变动。
    """
    if prev.list_type != curr.list_type:
        return ChangeResult(
            UNKNOWN, None,
            f"口径不一致：{prev.list_type} vs {curr.list_type}，禁止跨口径比较",
            False,
        )

    if prev.shares is None and curr.shares is None:
        return ChangeResult(UNKNOWN, None, "两期均未出现在名单中", False)
    if prev.shares is None:
        return ChangeResult(
            LIST_ENTRY, None,
            "本期新进名单：可能是首次买入，也可能只是排名上升进入前十，不能判定为增持",
            False,
        )
    if curr.shares is None:
        return ChangeResult(
            LIST_EXIT, None,
            "本期退出名单：可能是减持，也可能是被其他股东挤出前十，不能判定为减持",
            False,
        )

    adjusted_prev = prev.shares * corporate_action_factor
    delta = curr.shares - adjusted_prev

    if abs(delta) < EPS:
        if abs(corporate_action_factor - 1.0) > EPS:
            return ChangeResult(
                PASSIVE_ONLY, 0.0,
                f"持股数变化完全由公司行为（复权因子 {corporate_action_factor:g}）解释，不计入主动增减持",
                False,
            )
        return ChangeResult(UNCHANGED, 0.0, "复权后持股数无变化", False)

    label = INCREASE if delta > 0 else DECREASE
    return ChangeResult(
        label, round(delta, 2),
        f"复权前期初 {adjusted_prev:,.0f} 股 → 期末 {curr.shares:,.0f} 股，差额 {delta:,.0f} 股",
        True,
    )


def effective_date_of(report_period_end: str, disclose_date: str) -> str:
    """PIT 最早可用日 = 实际披露日（不得早于报告期末）。"""
    if not disclose_date:
        raise ValueError("披露日缺失，无法构造 PIT 时点")
    if disclose_date < report_period_end:
        raise ValueError(
            f"披露日 {disclose_date} 早于报告期末 {report_period_end}，数据不自洽"
        )
    return disclose_date


T = TypeVar("T")


def pit_filter(records: Iterable[T], as_of: str, key: str = "effective_date") -> list[T]:
    """只保留在 as_of 当日或之前真实可得的记录，防止前视偏差。"""
    out = []
    for r in records:
        value = r.get(key) if isinstance(r, dict) else getattr(r, key, None)
        if value is None or str(value) <= as_of:
            out.append(r)
    return out


def assert_no_lookahead(records: Iterable[T], as_of: str, key: str = "effective_date") -> None:
    """回测前置校验：任何晚于 as_of 的记录都视为契约违规。"""
    for r in records:
        value = r.get(key) if isinstance(r, dict) else getattr(r, key, None)
        if value is not None and str(value) > as_of:
            raise ValueError(f"前视偏差：存在 {key}={value} 晚于 as_of={as_of} 的记录")


def is_trading_day(d: Date) -> bool:
    """周末粗判；节假日需接入交易日历（见 README 已知限制）。"""
    return d.weekday() < 5
