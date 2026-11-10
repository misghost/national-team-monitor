"""量能观察层：成交额、环比、放量倍数的确定性计算。

对应《参考策略存档》第二、三节。这里只实现**客观可观测**的部分：

- 环比增量 / 环比增长率（当日成交额 vs 前一交易日）
- 放量倍数 = 当日成交额 / 前 5 日平均成交额
- 量能异动共振只数（放量 ≥ 阈值的标的数）

铁律（与《每日监控与进场决策清单》一致）：
1. **成交额放量不改变 ETF 总份额**。ETF 份额只由一级市场申赎决定，
   二级市场买卖无论多大量都不改变份额。因此放量**不能**推出国家队申购。
2. 放量倍数属 L3 辅助观测，无法识别买方身份，**不进入**三重共振判定
   （三重共振条件②只看「份额同步跳升」）。见 ``signals.py``。
3. 本模块不产生任何买/卖/仓位指令，不定义「买入信号」「卖出信号」。
   用户提供的分批交易规则作为参考原文存档于 docs/REFERENCE_STRATEGY.md，
   系统不执行。

所有函数为纯函数，无 I/O、无运行时推断，便于单测与复现。
"""
from __future__ import annotations

from dataclasses import dataclass

# 默认阈值与窗口（可用环境变量覆盖，见 config）
DEFAULT_SPIKE_MULTIPLE = 2.0
DEFAULT_MA_WINDOW = 5

MOMENTUM_CAVEAT = (
    "成交额放量属二级市场交易活跃度，不改变 ETF 总份额、无法识别买方身份，"
    "因此「放量 2 倍」不等于份额增加，更不等于国家队加仓。"
    "本节仅为客观量能观测，不构成信号判据，不提供任何操作建议。"
)


def amount_change(current: float, previous: float) -> float:
    """环比增量（亿元）。前一交易日为 0 时返回 0，避免除零。"""
    if previous is None or current is None:
        return 0.0
    return round(float(current) - float(previous), 4)


def amount_change_pct(current: float, previous: float) -> float:
    """环比增长率（%）。前值为 0 或负时返回 0（口径不可计算）。"""
    if previous is None or current is None or float(previous) <= 0:
        return 0.0
    return round((float(current) / float(previous) - 1) * 100, 2)


def moving_average(values: list[float]) -> float:
    """简单移动平均；空序列返回 0。"""
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return 0.0
    return round(sum(nums) / len(nums), 4)


def volume_multiple(current: float, ma_baseline: float) -> float:
    """放量倍数 = 当日成交额 / 前 N 日平均成交额。

    基线为 0 时返回 0（表示「不可计算」，而非「无限放量」）。
    """
    if current is None or ma_baseline is None or float(ma_baseline) <= 0:
        return 0.0
    return round(float(current) / float(ma_baseline), 2)


def is_spike(multiple: float, threshold: float = DEFAULT_SPIKE_MULTIPLE) -> bool:
    """是否达到放量阈值。仅为客观观测标记，不含任何归因含义。"""
    return float(multiple) >= float(threshold)


@dataclass(frozen=True)
class MomentumRow:
    """单只 ETF 某交易日的量能观测。"""
    code: str
    name: str
    amount_yi: float
    amount_prev_yi: float
    amount_ma_yi: float
    amount_change_yi: float
    amount_change_pct: float
    volume_multiple: float
    is_spike: bool
    is_2026_focus: bool
    share_change_yi: float          # 同期份额变化（亿元估算），用于对照
    note: str

    def as_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name,
            "amount_yi": self.amount_yi, "amount_prev_yi": self.amount_prev_yi,
            "amount_ma_yi": self.amount_ma_yi,
            "amount_change_yi": self.amount_change_yi,
            "amount_change_pct": self.amount_change_pct,
            "volume_multiple": self.volume_multiple,
            "is_spike": self.is_spike,
            "is_2026_focus": self.is_2026_focus,
            "share_change_yi": self.share_change_yi,
            "note": self.note,
        }


def build_row(
    *, code: str, name: str, current: float, previous: float,
    history: list[float], is_2026_focus: bool = False,
    share_change_yi: float = 0.0, threshold: float = DEFAULT_SPIKE_MULTIPLE,
    note: str = "L3 辅助观测 · 不可归因",
) -> MomentumRow:
    """由原始成交额序列构造一行量能观测（纯函数）。

    ``history`` 为当前日之前的 N 个交易日成交额（不含当日）。
    """
    ma = moving_average(history)
    mult = volume_multiple(current, ma)
    return MomentumRow(
        code=code, name=name,
        amount_yi=round(float(current), 4),
        amount_prev_yi=round(float(previous), 4),
        amount_ma_yi=ma,
        amount_change_yi=amount_change(current, previous),
        amount_change_pct=amount_change_pct(current, previous),
        volume_multiple=mult,
        is_spike=is_spike(mult, threshold),
        is_2026_focus=is_2026_focus,
        share_change_yi=round(float(share_change_yi), 4),
        note=note,
    )


@dataclass(frozen=True)
class MomentumSummary:
    """全池某交易日的量能汇总（中性描述，不含买卖指令）。"""
    date: str
    total_etf: int
    spike_count: int
    focus_spike_count: int
    threshold: float
    spike_codes: tuple[str, ...]
    observation: str
    caveat: str
    is_signal: bool = False      # 恒为 False：量能观测不是托底信号

    def as_dict(self) -> dict:
        return {
            "date": self.date, "total_etf": self.total_etf,
            "spike_count": self.spike_count, "focus_spike_count": self.focus_spike_count,
            "threshold": self.threshold, "spike_codes": list(self.spike_codes),
            "observation": self.observation, "caveat": self.caveat,
            "is_signal": self.is_signal,
        }


def summarize(
    date: str, rows: list[MomentumRow], threshold: float = DEFAULT_SPIKE_MULTIPLE,
) -> MomentumSummary:
    """汇总当日量能异动。observation 为纯观测描述，绝不出现买卖措辞。"""
    spikes = [r for r in rows if r.is_spike]
    focus_spikes = [r for r in spikes if r.is_2026_focus]
    if spikes:
        # 份额同步对照：只有一级市场净申购（份额增加）才可能对应资金流入，
        # 二级市场成交额放量本身不改变份额。明确点出二者是否背离。
        share_up = [r for r in spikes if r.share_change_yi > 0]
        if len(share_up) < len(spikes):
            divergence = (
                f"其中仅 {len(share_up)}/{len(spikes)} 只同时出现份额净增（L2 估算）；"
                "其余放量并未伴随份额增加，说明成交额放量不能推出申购或加仓。"
            )
        else:
            divergence = (
                f"本次 {len(share_up)} 只放量标的均伴随份额净增（L2 估算，仍无法识别申购人）；"
                "是否构成托底信号须另按三重共振条件判定，与本节量能观测无关。"
            )
        obs = (
            f"当日共 {len(spikes)}/{len(rows)} 只宽基 ETF 成交额放量 ≥{threshold} 倍"
            f"（其中 2026 重点观察标的 {len(focus_spikes)} 只）。{divergence}"
        )
    else:
        obs = (
            f"当日无宽基 ETF 成交额放量达到 {threshold} 倍阈值，量能整体平稳。"
        )
    return MomentumSummary(
        date=date, total_etf=len(rows), spike_count=len(spikes),
        focus_spike_count=len(focus_spikes), threshold=threshold,
        spike_codes=tuple(r.code for r in spikes),
        observation=obs, caveat=MOMENTUM_CAVEAT, is_signal=False,
    )


__all__ = [
    "DEFAULT_SPIKE_MULTIPLE", "DEFAULT_MA_WINDOW", "MOMENTUM_CAVEAT",
    "amount_change", "amount_change_pct", "moving_average", "volume_multiple",
    "is_spike", "MomentumRow", "MomentumSummary", "build_row", "summarize",
]
