"""历史对照样本。文件由使用者事后填入，缺失时页面不显示这一块。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config

_REQUIRED = ("name", "start", "end", "peak_daily_net_yi", "cum_net_yi", "max_drawdown_pct")


def load_episodes(path: Path | None = None) -> list[dict[str, Any]]:
    """读取 data/history_reference.json。缺文件、坏 JSON 或字段不全的条目都跳过。"""
    source = path or (config.DATA_DIR / "history_reference.json")
    if not source.exists():
        return []
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    episodes: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or any(key not in item for key in _REQUIRED):
            continue
        try:
            episodes.append({
                "name": str(item["name"]),
                "start": str(item["start"]),
                "end": str(item["end"]),
                "peak_daily_net_yi": float(item["peak_daily_net_yi"]),
                "cum_net_yi": float(item["cum_net_yi"]),
                "max_drawdown_pct": float(item["max_drawdown_pct"]),
                "note": str(item.get("note") or ""),
            })
        except (TypeError, ValueError):
            continue
    return episodes
