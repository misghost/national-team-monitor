"""人工确权的稳市事件清单（L3）。

铁律：事件清单**只能由人工核对公开来源后写入**，系统不从新闻标题、
不从行情异动、不从 ETF 份额推断事件。文件缺失或字段不全时返回空列表，
事件研究会明确报告「无可核验事件」，绝不自动生成事件填充样本。

t=0 口径：**意图公开日**，不是资金成交日。汇金/诚通/国新公告不含买入金额
与成交时点；互换便利、回购增持再贷款存在「额度→签约→发放→执行→二级买入」
的多级折损与时滞，额度、中标、签约金额均不能当作「当日入市资金」。
同一政策簇（如 2024-09-24 工具创设 + 诚旸增持 + 国新办发布会）必须合并为
一个样本，不得拆成多个独立事件虚增样本量。

确权等级由 verified_by 字段显式标注（官方原文 / 公开来源核实 / 媒体转引），
系统不把「WebSearch 核实」表述为「已逐条比对官方原文」。

格式见 deploy/verified_policy_events.example.json。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config

_REQUIRED = ("date", "name", "source", "verified")


def load_events(path: Path | None = None) -> list[dict[str, Any]]:
    """读取 data/verified_policy_events.json。坏 JSON / 字段缺失 / 未确权一律跳过。"""
    source = path or (config.DATA_DIR / "verified_policy_events.json")
    if not source.exists():
        return []
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    events: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or any(k not in item for k in _REQUIRED):
            continue
        if item.get("verified") is not True:
            continue
        try:
            day = str(item["date"]).strip()
            if len(day) != 10 or day[4] != "-" or day[7] != "-":
                continue
            events.append({
                "date": day,
                "name": str(item["name"]),
                "source": str(item["source"]),
                "verified_by": str(item.get("verified_by") or "未标注核验人"),
                "note": str(item.get("note") or ""),
            })
        except (TypeError, ValueError):
            continue
    return sorted(events, key=lambda e: e["date"])
