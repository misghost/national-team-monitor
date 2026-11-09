"""三重共振「托底信号」判定。

严格遵循《每日监控与进场决策清单》：
- 条件① 市场大跌/连续下跌
- 条件② 多只核心宽基 ETF 份额同步跳升（看份额，不是成交额）
- 条件③ 官方增持类公告配合
三者齐备才记为「政策底信号（推断）」；缺任一条件只能记为
「有大额增量资金承接」，绝不标记为国家队的队进场。

铁律：is_confirmed 永远为 False；基于 L2 的推断最高只能给 medium 置信度。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config
from .layers import CONFIDENCE_LABEL, L2, attribution_text

# 状态枚举
POLICY_BOTTOM_PROBABLE = "POLICY_BOTTOM_PROBABLE"
INFLOW_ONLY = "INFLOW_ONLY"
ANNOUNCEMENT_ONLY = "ANNOUNCEMENT_ONLY"
NO_SIGNAL = "NO_SIGNAL"

STATE_LABEL = {
    POLICY_BOTTOM_PROBABLE: "政策底信号（推断，非实锤）",
    INFLOW_ONLY: "仅观测到增量资金承接",
    ANNOUNCEMENT_ONLY: "仅有公告，无资金面印证",
    NO_SIGNAL: "无托底信号",
}

CAVEAT = (
    "本信号为基于 L2 估算与 L3 公告的概率推断，无法确权到具体主体；"
    "政策底不等于市场底（历史间隔 40 天至 6 个月），"
    "国家队入场可增强信心但不宜成为抄底理由。系统不提供个股买卖建议。"
)


@dataclass(frozen=True)
class SignalInput:
    date: str
    index_change_pct: float
    index_change_5d_pct: float
    sync_etf_count: int
    net_subscription_yi: float
    supportive_announcements: int


@dataclass(frozen=True)
class Thresholds:
    market_drop_1d_pct: float = config.MARKET_DROP_1D_PCT
    market_drop_5d_pct: float = config.MARKET_DROP_5D_PCT
    etf_sync_min: int = config.ETF_SYNC_MIN
    etf_net_min_yi: float = config.ETF_NET_MIN_YI
    official_lookback_days: int = config.OFFICIAL_LOOKBACK_DAYS


def evaluate(inp: SignalInput, th: Thresholds | None = None) -> dict:
    """返回符合 API 契约的 signal dict。"""
    th = th or Thresholds()

    market_met = (
        inp.index_change_pct <= th.market_drop_1d_pct
        or inp.index_change_5d_pct <= th.market_drop_5d_pct
    )
    etf_met = (
        inp.sync_etf_count >= th.etf_sync_min
        and inp.net_subscription_yi >= th.etf_net_min_yi
    )
    official_met = inp.supportive_announcements > 0

    conditions = {
        "market_drop": {
            "met": market_met,
            "label": "市场大跌 / 连续下跌",
            "detail": (
                f"当日 {inp.index_change_pct:+.2f}%，近 5 日 {inp.index_change_5d_pct:+.2f}%"
                f"（阈值：单日 ≤{th.market_drop_1d_pct}% 或 5 日 ≤{th.market_drop_5d_pct}%）"
            ),
        },
        "etf_sync": {
            "met": etf_met,
            "label": "多只核心宽基份额同步跳升",
            "detail": (
                f"{inp.sync_etf_count} 只同步净申购，合计约 {inp.net_subscription_yi:,.1f} 亿元"
                f"（阈值：≥{th.etf_sync_min} 只且 ≥{th.etf_net_min_yi:,.0f} 亿元）；"
                "数据来自 ETF 总份额，非成交额"
            ),
        },
        "official": {
            "met": official_met,
            "label": "官方增持类公告配合",
            "detail": (
                f"近 {th.official_lookback_days} 日 {inp.supportive_announcements} 条增持/稳市类公告"
            ),
        },
    }

    met_all = market_met and etf_met and official_met
    if met_all:
        state = POLICY_BOTTOM_PROBABLE
        confidence = "medium"
        summary = (
            "三重共振成立：市场急跌、多只核心宽基份额同步大幅跳升、且有官方增持类公告配合。"
            "可记为「政策底信号」，但仍是推断而非实锤——ETF 份额无法识别申购人身份。"
        )
    elif etf_met:
        state = INFLOW_ONLY
        confidence = "low"
        summary = (
            "仅观测到大额增量资金通过宽基 ETF 承接。保险、公募自营、外资、国有资本"
            "都可能是一级市场申购方，缺少官方公告确权，**不标记为国家队进场**。"
        )
    elif official_met:
        state = ANNOUNCEMENT_ONLY
        confidence = "low"
        summary = (
            "有官方增持/稳市类公告，但 ETF 份额未出现同步大额净申购。"
            "公告表达政策意图，额度不等于实际买入金额，需等待资金面印证。"
        )
    else:
        state = NO_SIGNAL
        confidence = "low"
        summary = "三重条件均未满足，无托底信号。"

    if state == INFLOW_ONLY and conditions["market_drop"]["met"] is False:
        summary += " 此外市场未处于急跌环境，逆周期托底逻辑不成立。"

    return {
        "date": inp.date,
        "state": state,
        "state_label": STATE_LABEL[state],
        "confidence": confidence,
        "confidence_label": CONFIDENCE_LABEL[confidence],
        # 铁律：公开数据无法确权，永远为 False
        "is_confirmed": False,
        "attribution": attribution_text(L2),
        "summary": summary,
        "conditions": conditions,
        "caveat": CAVEAT,
    }
