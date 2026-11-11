"""宽基长趋势。

净申购只来自 etf_daily 的官方估算（份额变化 × 净值）。
成交额和收盘价在官方记录缺失时，回退到 data/etf_kline.json 的公开行情。
没有行情文件时，只返回库里已有的官方日期。
"""
from __future__ import annotations

import json
from typing import Any

from . import config, db
from .etf_pool import pool_codes

# 与首页宽基表同一套短名。
SHORT_NAMES = {
    "510050": "华夏上证50",
    "510300": "华泰柏瑞沪深300",
    "510500": "南方中证500",
    "512100": "南方中证1000",
    "159845": "华夏中证1000",
    "588000": "华夏科创50",
    "159915": "易方达创业板",
}


def load_kline(path=None) -> dict | None:
    """读取行情文件。缺文件、坏 JSON 或结构不对时返回 None，调用方只画官方记录。"""
    source = path or (config.DATA_DIR / "etf_kline.json")
    if not source.exists():
        return None
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return round(number, 4)


def _kline_by_code(payload: dict | None) -> dict[str, dict[str, dict[str, float | None]]]:
    """code -> date -> {amount_yi, close}。文件声明的 as_of 之后的行情点丢掉。"""
    tables: dict[str, dict[str, dict[str, float | None]]] = {}
    if not payload:
        return tables
    as_of = payload.get("as_of")
    cap = str(as_of) if isinstance(as_of, str) and as_of else None
    items = payload.get("items")
    if not isinstance(items, list):
        return tables
    allowed = set(pool_codes())
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "")
        if code not in allowed:
            continue
        points = item.get("points")
        if not isinstance(points, list):
            continue
        bucket = tables.setdefault(code, {})
        for point in points:
            if not isinstance(point, dict):
                continue
            day = str(point.get("date") or "")
            if len(day) < 8:
                continue
            if cap and day > cap:
                continue
            bucket[day] = {
                "amount_yi": _num(point.get("amount_yi")),
                "close": _num(point.get("close")),
            }
    return tables


def _official_by_code(rows: list[dict]) -> dict[str, dict[str, dict[str, float | None]]]:
    tables: dict[str, dict[str, dict[str, float | None]]] = {}
    allowed = set(pool_codes())
    for row in rows:
        code = str(row.get("code") or "")
        if code not in allowed:
            continue
        day = str(row.get("date") or "")
        if not day:
            continue
        tables.setdefault(code, {})[day] = {
            "net_yi": _num(row.get("est_net_yi")),
            "amount_yi": _num(row.get("amount_yi")),
            "close": _num(row.get("nav")),
        }
    return tables


def build_history(official_rows: list[dict], kline_payload: dict | None, days: int = 250) -> dict:
    """按日期合并。days 是截至 as_of 往前保留的交易日个数。"""
    official = _official_by_code(official_rows)
    kline = _kline_by_code(kline_payload)
    dates = set()
    for table in official.values():
        dates.update(table)
    for table in kline.values():
        dates.update(table)
    ordered = sorted(dates)
    window = ordered[-days:] if days < len(ordered) else ordered
    items = []
    for code in pool_codes():
        off = official.get(code, {})
        quote = kline.get(code, {})
        points = []
        for day in window:
            if day not in off and day not in quote:
                continue
            if day in off:
                net = off[day]["net_yi"]
                amount = off[day]["amount_yi"]
                close = off[day]["close"]
                if amount is None and day in quote:
                    amount = quote[day]["amount_yi"]
                if close is None and day in quote:
                    close = quote[day]["close"]
            else:
                net = None
                amount = quote[day]["amount_yi"]
                close = quote[day]["close"]
            if net is None and amount is None and close is None:
                continue
            points.append({
                "date": day,
                "net_yi": net,
                "amount_yi": amount,
                "close": close,
            })
        items.append({"code": code, "name": SHORT_NAMES[code], "points": points})
    return {"as_of": ordered[-1] if ordered else "", "items": items}


def _read_official() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT date, code, name, est_net_yi, amount_yi, nav FROM etf_daily"
        ).fetchall()
    return [dict(row) for row in rows]


def etf_history(days: int = 250) -> dict:
    return build_history(_read_official(), load_kline(), days)
