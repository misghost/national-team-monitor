"""个人节奏分析增强版。

核心思路：
- 用核心宽基 ETF 一级市场份额变化作为最强公开代理
- 结合指数跌幅、多日累计净申购、量能放大做综合观察
- 输出可操作的「关注强度」而非买卖指令

仍然无法日频确权到具体国家队账户。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from . import db
from .signals import STATE_LABEL


def _row_net_yi(row: dict[str, Any]) -> float:
    """读取一日净申购（亿元）。

    ``db.get_etf_series`` 返回的是按日汇总后的 ``net_subscription_yi``。
    若调用方传入逐只 ETF 记录，则使用 ``est_net_yi`` 再按日相加。
    """
    if "est_net_yi" in row and row.get("est_net_yi") is not None:
        return float(row["est_net_yi"])
    return float(row.get("net_subscription_yi") or 0.0)


def recent_etf_flows(days: int = 10) -> list[dict[str, Any]]:
    """最近 N 个交易日核心宽基合计净申购（亿元）。"""
    db.init_db()
    series = db.get_etf_series(days=days * 3)
    if not series:
        return []

    by_day: dict[str, float] = {}
    for r in series:
        by_day[r["date"]] = by_day.get(r["date"], 0.0) + _row_net_yi(r)

    out = [{"date": d, "net_yi": round(by_day[d], 2)} for d in sorted(by_day.keys())]
    return out[-days:]


def cumulative_flow(days: int = 5) -> float:
    flows = recent_etf_flows(days)
    return round(sum(x["net_yi"] for x in flows), 2)


def personal_score(as_of: str | None = None) -> dict[str, Any]:
    """个人「政策托底关注强度」打分（0-85）。

    逻辑可自行在此调整：
    - 近5日指数跌幅
    - 近3日 / 近5日核心宽基净申购规模
    - 官方增持类公告
    """
    db.init_db()
    as_of = as_of or date.today().isoformat()
    signals = db.get_signals(days=5)
    latest = signals[-1] if signals else None
    if latest is not None and not latest.get("state_label"):
        # get_signals 只返回 state 代码，日报需要中文标签
        latest = dict(latest)
        state = str(latest.get("state") or "")
        latest["state_label"] = STATE_LABEL.get(state, state)
    latest_date = latest["date"] if latest else as_of

    market_row = db.get_market_row(latest_date)
    flows = recent_etf_flows(7)
    cum3 = round(sum(x["net_yi"] for x in flows[-3:]), 2) if flows else 0.0
    cum5 = round(sum(x["net_yi"] for x in flows[-5:]), 2) if flows else 0.0

    score = 25
    reasons: list[str] = []

    if market_row:
        chg1 = market_row.get("index_change_pct") or 0.0
        chg5 = market_row.get("index_change_5d_pct") or 0.0
        if chg5 <= -5 or chg1 <= -2.5:
            score += 28
            reasons.append(f"指数明显下跌（当日 {chg1:+.1f}%，近5日 {chg5:+.1f}%）")
        elif chg5 <= -2.5 or chg1 <= -1.5:
            score += 16
            reasons.append(f"指数有回调（当日 {chg1:+.1f}%，近5日 {chg5:+.1f}%）")

    if cum3 >= 400:
        score += 32
        reasons.append(f"近3日核心宽基净申购约 {cum3:.0f} 亿（强）")
    elif cum3 >= 200:
        score += 20
        reasons.append(f"近3日核心宽基净申购约 {cum3:.0f} 亿")
    elif cum3 >= 80:
        score += 10
        reasons.append(f"近3日核心宽基净申购约 {cum3:.0f} 亿")
    elif cum3 <= -300:
        score -= 18
        reasons.append(f"近3日核心宽基净赎回约 {abs(cum3):.0f} 亿")

    anns = db.get_announcements(limit=8)
    supportive = sum(1 for a in anns if a.get("is_supportive"))
    if supportive >= 1:
        score += 12
        reasons.append(f"近期待有 {supportive} 条官方增持/稳市类公告")

    score = max(0, min(85, score))
    level = "强" if score >= 70 else "中" if score >= 48 else "弱"

    return {
        "as_of": latest_date,
        "score": score,
        "level": level,
        "reasons": reasons or ["暂无显著共振"],
        "cum3_yi": cum3,
        "cum5_yi": cum5,
        "recent_flows": flows,
        "latest_signal": latest,
        "note": (
            "分数仅综合公开代理指标，供个人观察市场节奏参考。"
            "无法确权到国家队具体账户，政策底不等于市场底，不构成投资建议。"
        ),
    }
