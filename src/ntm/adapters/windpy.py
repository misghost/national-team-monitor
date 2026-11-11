"""WindPy 本地适配器（个人使用，需本机已登录 Wind 终端）。

取数口径与 WindMCPAdapter 保持一致：
- 指数日K：w.wsd("000300.SH", "close,amt", ...)
- ETF 场内流通份额：优先使用 f_unit_floortrading / unit_floortrading
- 净值：优先 IOPV，其次 unit_nav
- 成交额仅作量能观测，绝不替代份额

字段名可能因 Wind 版本略有差异，首次使用建议在终端用「超级命令」确认后
通过环境变量 NTM_WIND_SHARE_FIELD 覆盖。

凭据由 Wind 终端自身持有。本模块不读取、不打印、不落盘任何账号或口令。
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .. import config
from ..datasource import LiveDataError, validate_dataset
from .base import BaseAdapter, FetchResult

SOURCE_NAME = "windpy:wsd"
INDEX_CODE = "000300.SH"
HISTORY_FILENAME = "windpy_history.json"
ANNOUNCEMENT_FILENAME = "verified_announcements.json"

# 常见份额字段候选（按优先级）
SHARE_FIELD_CANDIDATES = (
    "f_unit_floortrading",
    "unit_floortrading",
    "fund_unit_floortrading",
    "unit_total",
)


def _wind_code(code: str) -> str:
    return code + (".SH" if code.startswith("5") else ".SZ")


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


class WindPyAdapter(BaseAdapter):
    """本机 Wind 终端 + WindPy 取数适配器。"""

    name = "windpy"
    verified = True

    def __init__(self, history_path: Path | None = None):
        self.history_path = history_path or (config.DATA_DIR / HISTORY_FILENAME)
        self.announcement_path = config.DATA_DIR / ANNOUNCEMENT_FILENAME
        self.pool = {code: name for code, name in config.CORE_ETF}
        self.backfill_days = config.wind_backfill_days()

    def _connect(self):
        try:
            from WindPy import w
        except ImportError as exc:
            raise LiveDataError(
                "未安装 WindPy。请在本机 Wind 终端环境下运行，或先安装终端自带的 WindPy"
            ) from exc
        if not w.isconnected():
            w.start()
        if not w.isconnected():
            raise LiveDataError("Wind 终端未登录或连接失败，请先启动并登录 Wind 客户端")
        return w

    def _load_history(self) -> dict[str, Any]:
        if self.history_path.exists():
            try:
                data = json.loads(self.history_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("days"), dict):
                    return data
            except (OSError, ValueError):
                pass
        return {"version": 1, "days": {}}

    def _save_history(self, history: dict[str, Any]) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.history_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.history_path)

    def _share_field(self) -> str:
        return (
            os.getenv("NTM_WIND_SHARE_FIELD", "").strip()
            or SHARE_FIELD_CANDIDATES[0]
        )

    def _manual_announcements(self, as_of: str) -> list[dict[str, Any]]:
        """L3 只接受人工核验文件，系统不根据标题推断 is_supportive。"""
        if not self.announcement_path.exists():
            return []
        try:
            items = json.loads(self.announcement_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(items, list):
            return []
        out: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if not all(k in item for k in ("date", "source", "title", "type", "is_supportive")):
                continue
            if str(item["date"]) > as_of:
                continue
            out.append({
                "date": str(item["date"]),
                "source": str(item["source"]),
                "title": str(item["title"]),
                "type": str(item["type"]),
                "is_supportive": bool(item["is_supportive"]),
                "url": str(item.get("url", "")),
                "layer": "L3",
            })
        return out

    def _fetch_kline(self, w, as_of: str) -> list[dict[str, Any]]:
        end = date.fromisoformat(as_of)
        begin = end - timedelta(days=config.wind_kline_days())
        err, df = w.wsd(
            INDEX_CODE,
            "close,amt",
            begin.isoformat(),
            end.isoformat(),
            "Fill=Previous;PriceAdj=F",
            usedf=True,
        )
        if err != 0 or df is None or df.empty:
            raise LiveDataError(f"WindPy 指数K线取数失败 err={err}")

        series: list[dict[str, Any]] = []
        for dt, row in df.iterrows():
            day = str(dt)[:10]
            if day > as_of:
                continue
            close = _num(row.get("CLOSE") or row.get("close"))
            if close is None or close <= 0:
                continue
            amt = _num(row.get("AMT") or row.get("amt"))
            series.append({"date": day, "_close": close, "_amount": amt})

        if len(series) < 6:
            raise LiveDataError("指数有效交易日不足，无法计算5日涨跌幅")

        closes = [r["_close"] for r in series]
        for i, row in enumerate(series):
            prev = closes[i - 1] if i >= 1 else closes[i]
            base5 = closes[i - 5] if i >= 5 else closes[0]
            amount_yi = (row["_amount"] / 1e8) if row["_amount"] else None
            row.update({
                "index_close": row["_close"],
                "index_change_pct": round((row["_close"] / prev - 1) * 100, 4),
                "index_change_5d_pct": round((row["_close"] / base5 - 1) * 100, 4),
                "amount_yi": round(amount_yi, 4) if amount_yi is not None else None,
            })
            row.pop("_close", None)
            row.pop("_amount", None)
        return series

    def _fetch_etf_day(self, w, day: str) -> dict[str, dict[str, Any]]:
        codes = [_wind_code(c) for c in self.pool]
        share_field = self._share_field()
        # 一次取多品种份额 + 净值
        fields = f"{share_field},unit_nav,iopv"
        err, df = w.wss(
            ",".join(codes),
            fields,
            f"tradeDate={day};unit=1",
            usedf=True,
        )
        if err != 0 or df is None or df.empty:
            raise LiveDataError(f"WindPy ETF 份额取数失败 err={err} day={day}")

        snap: dict[str, dict[str, Any]] = {}
        for wind_code, row in df.iterrows():
            code = str(wind_code).split(".")[0]
            if code not in self.pool:
                continue
            total = _num(row.get(share_field.upper()) or row.get(share_field))
            nav = _num(row.get("IOPV") or row.get("iopv") or row.get("UNIT_NAV") or row.get("unit_nav"))
            if total is None or total <= 0:
                continue
            snap[code] = {
                "code": code,
                "name": self.pool[code],
                "total_share": total,
                "nav": nav or 0.0,
                "source": SOURCE_NAME,
            }
        return snap

    def _fetch_impl(self, as_of: str) -> FetchResult:
        w = self._connect()
        history = self._load_history()
        days: dict[str, Any] = history.setdefault("days", {})

        # 补抓最近若干自然日（多取一些以防周末），非交易日跳过
        end = date.fromisoformat(as_of)
        need_days = []
        cur = end
        for _ in range(self.backfill_days * 2):
            d = cur.isoformat()
            if d not in days:
                need_days.append(d)
            cur -= timedelta(days=1)
            if len(need_days) >= self.backfill_days:
                break

        for d in reversed(need_days):  # 从早到晚
            try:
                snap = self._fetch_etf_day(w, d)
                if snap:
                    days[d] = snap
            except LiveDataError:
                # 非交易日或无数据则跳过
                continue

        market = self._fetch_kline(w, as_of)
        etf_rows: list[dict[str, Any]] = []
        for day_str, snap in sorted(days.items()):
            if day_str > as_of:
                continue
            prev_day = None
            # 简单取前一个有数据的交易日计算变化
            for pd in sorted(days.keys(), reverse=True):
                if pd < day_str:
                    prev_day = days[pd]
                    break
            for code, cur_row in snap.items():
                prev_share = (prev_day or {}).get(code, {}).get("total_share")
                change = (cur_row["total_share"] - prev_share) if prev_share else 0.0
                nav = cur_row.get("nav") or 1.0
                est = change * nav / 1e8
                etf_rows.append({
                    "date": day_str,
                    "code": code,
                    "name": cur_row["name"],
                    "total_share": cur_row["total_share"],
                    "share_change": change,
                    "nav": nav,
                    "est_net_yi": round(est, 4),
                    "amount_yi": 0.0,  # 量能可后续补充；不得用来顶替份额
                    "layer": "L2",
                    "attributable": False,
                    "source": SOURCE_NAME,
                })

        latest = max((r["date"] for r in etf_rows), default="")
        if latest:
            market = [r for r in market if r["date"] <= latest]

        dataset = {
            "as_of": as_of,
            "is_demo": False,
            "source": SOURCE_NAME,
            "data_latest_date": latest,
            "market": market,
            "etf": etf_rows,
            "announcements": self._manual_announcements(as_of),
            "holdings": [],
        }

        problems = validate_dataset(dataset, as_of)
        if problems:
            raise LiveDataError(
                "WindPy 真实数据质量校验未通过（已丢弃本次采集，不入库、不降级）："
                + "；".join(problems[:12])
            )
        # 校验通过后再落盘，避免把未过闸门的快照留在历史文件里
        self._save_history(history)
        return FetchResult(
            dataset=dataset,
            source=SOURCE_NAME,
            is_demo=False,
            verified=True,
            warnings=[
                "真实数据已接入，但 L2 仍不可归因：无法识别 ETF 申购人身份",
                "所有信号 is_confirmed 恒为 False，本系统不提供买卖建议",
                "份额字段名未经本仓库实测，请用超级命令确认后设置 NTM_WIND_SHARE_FIELD",
                "L3 官方公告仅接受人工核验录入，系统不自动判定",
            ],
        )

    def fetch(self, as_of: str | None = None) -> FetchResult:
        as_of = as_of or date.today().isoformat()
        try:
            date.fromisoformat(as_of)
        except (TypeError, ValueError):
            raise LiveDataError(f"as_of 不是合法 ISO 日期：{as_of}") from None
        if not config.data_verified():
            raise LiveDataError(
                "NTM_DATA_VERIFIED=0：数据源核验清单未完成。已停止采集，不会降级到演示数据。"
            )
        try:
            return self._fetch_impl(as_of)
        except LiveDataError:
            raise
        except Exception as exc:
            # 第三方异常信息可能夹带终端路径或账号，只保留类型名
            raise LiveDataError(f"WindPy 取数异常：{type(exc).__name__}") from None
