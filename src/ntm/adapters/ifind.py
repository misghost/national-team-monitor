"""iFinD 适配器（个人使用）。

两条路：
1. Linux 服务器：HTTP refresh_token（不需要 iFinDPy）。见 ifind_http.py。
2. 本机已装 iFinDPy 且只配置了账号密码时，走 SDK。SDK 的返回格式仍未在本仓库核对，
   未核对完会失败停止，不会写入空数据。

指数收盘价使用技能包确认过的历史行情指标 ``close``。
场内流通份额和净值的指标代码不在 v1.1.0 参考文档里，必须由环境变量显式给出，
否则直接失败。

凭据只从环境变量读取，绝不硬编码、不打印、不落盘，也不挂到实例属性上。
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
from .ifind_http import (
    CONFIRMED_DS_INDICATOR,
    IFindHttp,
    http_credentials_configured,
    scrub,
)

SOURCE_NAME = "ifind:http"
INDEX_CODE = "000300.SH"
HISTORY_FILENAME = "ifind_history.json"
ANNOUNCEMENT_FILENAME = "verified_announcements.json"

# 2026-11-09 线上核对：这两个指标与 Wind 场内份额算出的净申购一致。
# 技能包 v1.1.0 没有收录它们，不要再猜别的代码。参数模板是空数组。
VERIFIED_SHARE_FIELD = "ths_inner_float_shares_fund"
VERIFIED_NAV_FIELD = "ths_iopv_fund"
SHARE_FIELD_TODO = (
    "NTM_IFIND_SHARE_FIELD 已设置但没有 NTM_IFIND_SHARE_PARAMS。"
    f"线上核对过的默认是 {VERIFIED_SHARE_FIELD}，参数 []。"
    "自定义指标必须带上 get_indicator_params 的顺序，没有参数就填 []。"
)
NAV_FIELD_TODO = (
    "NTM_IFIND_NAV_FIELD 已设置但没有 NTM_IFIND_NAV_PARAMS。"
    f"线上核对过的默认是 {VERIFIED_NAV_FIELD}，参数 []。"
    "不要用股票收盘价 "
    f"{CONFIRMED_DS_INDICATOR} 顶替 IOPV。"
)


def _ifind_code(code: str) -> str:
    return code + (".SH" if code.startswith("5") else ".SZ")


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


class IFindAdapter(BaseAdapter):
    """iFinD 数据适配器。"""

    name = "ifind"
    verified = True

    def __init__(self, history_path: Path | None = None, http: IFindHttp | None = None):
        self.history_path = history_path or (config.DATA_DIR / HISTORY_FILENAME)
        self.announcement_path = config.DATA_DIR / ANNOUNCEMENT_FILENAME
        self.pool = {code: name for code, name in config.CORE_ETF}
        self.backfill_days = config.wind_backfill_days()
        self._http = http

    def _connect_sdk(self, username: str, password: str):
        try:
            from iFinDPy import THS_BasicData, THS_HistoryQuotes, THS_iFinDLogin
            self._THS_HistoryQuotes = THS_HistoryQuotes
            self._THS_BasicData = THS_BasicData
        except ImportError as exc:
            raise LiveDataError("未安装 iFinDPy。请确认已安装同花顺量化接口") from exc

        if username and password:
            ret = THS_iFinDLogin(username, password)
            if ret != 0:
                # 只报告返回码，不回显账号或密码
                raise LiveDataError(f"iFinD 登录失败，返回码 {ret}")
        # 若已在终端登录，部分环境可不显式登录
        return True

    def _indicator_spec(
        self, field_env: str, params_env: str, default_name: str, missing: str,
    ) -> tuple[str, list[str]]:
        name = _env(field_env)
        raw = os.getenv(params_env)
        raw_text = "" if raw is None else raw.strip()
        if not name:
            name = default_name
            if raw_text == "":
                return name, []
        elif raw_text == "":
            raise LiveDataError(missing)
        raw = raw_text
        if raw == "[]":
            return name, []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except ValueError:
                raise LiveDataError(f"{params_env} 不是合法 JSON 数组") from None
            if not isinstance(parsed, list):
                raise LiveDataError(f"{params_env} 必须是 JSON 数组")
            return name, [str(item) for item in parsed]
        return name, [part.strip() for part in raw.split(",") if part.strip()]

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

    def _fetch_kline_sdk(self, as_of: str) -> list[dict[str, Any]]:
        end = date.fromisoformat(as_of)
        begin = end - timedelta(days=config.wind_kline_days())
        # THS_HistoryQuotes 返回格式需根据实际 SDK 调整；这里做兼容处理
        result = self._THS_HistoryQuotes(
            INDEX_CODE,
            "open;high;low;close;volume;amount",
            "Interval:D,Fill:Previous",
            begin.isoformat(),
            end.isoformat(),
        )
        # 实际返回结构因版本而异。未识别的结构直接停止，不用空数据冒充成功。
        if not result or (hasattr(result, "errorcode") and result.errorcode != 0):
            raise LiveDataError("iFinD 指数行情取数失败")
        raise LiveDataError(
            "iFinD SDK 返回格式需根据你的终端版本调整解析逻辑。"
            "建议先用超级命令确认字段后修改 _fetch_kline_sdk。"
        )

    def _manual_announcements(self, as_of: str) -> list[dict[str, Any]]:
        if not self.announcement_path.exists():
            return []
        try:
            items = json.loads(self.announcement_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(items, list):
            return []
        out = []
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

    def _fetch_http(self, as_of: str) -> FetchResult:
        share_name, share_params = self._indicator_spec(
            "NTM_IFIND_SHARE_FIELD", "NTM_IFIND_SHARE_PARAMS",
            VERIFIED_SHARE_FIELD, SHARE_FIELD_TODO,
        )
        nav_name, nav_params = self._indicator_spec(
            "NTM_IFIND_NAV_FIELD", "NTM_IFIND_NAV_PARAMS",
            VERIFIED_NAV_FIELD, NAV_FIELD_TODO,
        )
        client = self._http or IFindHttp()
        end = date.fromisoformat(as_of)
        begin = (end - timedelta(days=config.wind_kline_days())).isoformat()
        try:
            trade_days = [d for d in client.trade_dates(begin, as_of) if d <= as_of]
            closes = client.history_close(INDEX_CODE, begin, as_of)
        except LiveDataError:
            raise
        except Exception as exc:
            raise LiveDataError(scrub(f"iFinD HTTP 取数异常：{type(exc).__name__}")) from None

        market = _market_from_closes(closes, as_of)
        history = self._load_history()
        days: dict[str, Any] = history.setdefault("days", {})
        needed = [d for d in trade_days[-self.backfill_days:] if d not in days]
        if needed:
            codes = [_ifind_code(code) for code in self.pool]
            try:
                grid = client.date_sequence(
                    codes,
                    [(share_name, share_params), (nav_name, nav_params)],
                    needed,
                )
            except LiveDataError:
                raise
            except Exception as exc:
                raise LiveDataError(scrub(f"iFinD HTTP 取数异常：{type(exc).__name__}")) from None
            for day in needed:
                snap = {}
                for code, name in self.pool.items():
                    row = grid.get(code, {}).get(day) or {}
                    total = row.get(share_name)
                    nav = row.get(nav_name)
                    if total is None or total <= 0 or nav is None or nav <= 0:
                        raise LiveDataError(
                            f"iFinD {day} {code} 的份额或净值无效，已停止采集"
                        )
                    snap[code] = {
                        "name": name,
                        "total_share": total,
                        "nav": nav,
                    }
                days[day] = snap

        etf_rows = _etf_rows(days, as_of, self.pool)
        latest = max((row["date"] for row in etf_rows), default="")
        if latest:
            market = [row for row in market if row["date"] <= latest]
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
                "iFinD 数据质量校验未通过（已丢弃本次采集，不入库、不降级）："
                + "；".join(problems[:12])
            )
        self._save_history(history)
        return FetchResult(
            dataset=dataset,
            source=SOURCE_NAME,
            is_demo=False,
            verified=True,
            warnings=[
                "真实数据已接入，但 L2 仍不可归因：无法识别 ETF 申购人身份",
                "指数只用了文档确认的 close；历史行情文档没有确认成交额指标，amount 未请求",
                "份额与净值指标来自环境变量，技能包未收录这两个代码",
                "所有信号 is_confirmed 恒为 False，本系统不提供买卖建议",
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

        if http_credentials_configured():
            try:
                return self._fetch_http(as_of)
            except LiveDataError as exc:
                raise LiveDataError(scrub(str(exc))) from None

        username = _env("NTM_IFIND_USER")
        password = _env("NTM_IFIND_PASS")
        try:
            self._connect_sdk(username, password)
            raise LiveDataError(
                "iFinDPy 已连接，但 SDK 返回格式尚未按本仓库核对。"
                "Linux 服务器请改用 NTM_IFIND_REFRESH_TOKEN 的 HTTP 通道。"
                "完成前不会入库，也不会降级到演示数据。"
            )
        except LiveDataError:
            raise
        except Exception as exc:
            raise LiveDataError(f"iFinD 连接异常：{type(exc).__name__}") from None


def _market_from_closes(closes: list[tuple[str, float]], as_of: str) -> list[dict[str, Any]]:
    rows = [(day, close) for day, close in closes if day <= as_of and close > 0]
    if len(rows) < 6:
        raise LiveDataError("iFinD 指数有效交易日不足，无法计算 5 日涨跌幅")
    out = []
    for i, (day, close) in enumerate(rows):
        prev = rows[i - 1][1] if i else close
        base = rows[i - 5][1] if i >= 5 else rows[0][1]
        out.append({
            "date": day,
            "index_close": close,
            "index_change_pct": round((close / prev - 1) * 100, 4),
            "index_change_5d_pct": round((close / base - 1) * 100, 4),
            "amount_yi": None,
        })
    return out


def _etf_rows(days: dict[str, Any], as_of: str, pool: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ordered = [day for day in sorted(days) if day <= as_of]
    for index, day in enumerate(ordered):
        prev = days[ordered[index - 1]] if index else {}
        for code, current in days[day].items():
            if code not in pool:
                continue
            prev_share = (prev.get(code) or {}).get("total_share")
            change = (current["total_share"] - prev_share) if prev_share else 0.0
            nav = current["nav"]
            rows.append({
                "date": day,
                "code": code,
                "name": current.get("name") or pool[code],
                "total_share": current["total_share"],
                "share_change": change,
                "nav": nav,
                "est_net_yi": round(change * nav / 1e8, 4),
                "amount_yi": 0.0,
                "layer": "L2",
                "attributable": False,
                "source": SOURCE_NAME,
            })
    if not rows:
        raise LiveDataError("iFinD 未得到任何 ETF 份额，已停止采集")
    return rows
