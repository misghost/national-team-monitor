"""iFinD HTTP 数据 API（quantapi.51ifind.com）。

协议以 iFinD 官方技能包 v1.1.0 为准（scripts/_api_client.py、
get_access_token.py、references/data_functions 与 help_tools）：

- 数据基地址默认 ``https://quantapi.51ifind.com/api/v1``，可用
  ``IFIND_DATA_API_BASE_URL`` 覆盖。
- ``POST /get_access_token``，请求头 ``refresh_token``，响应
  ``data.access_token``。
- 数据请求头是 ``access_token``，不是 Authorization。
- ``history_data``（THS_HQ）的请求体必须包在 ``reqBody`` 里。
- ``date_sequence``（THS_DS）与 ``get_trade_dates`` 使用扁平 JSON。
- 交易日历的 ``tables`` 是对象，日期在 ``tables.time``。
- 行情与日期序列的 ``tables`` 是数组，每项含 ``thscode``、时间轴和列式 ``table``。
- access_token 失效的已文档化错误码是 -1302，refresh_token 失效是 -1301。
- 免费档频率按运营方实测：单函数 10 次/秒，合计 20 次/秒。

凭据只从环境变量读取，不写入磁盘、不放进异常文本。access_token 只缓存在内存，
失效或鉴权失败时用 refresh_token 重换一次。
"""
from __future__ import annotations

import os
import time
from collections import deque
from typing import Any, Callable

from ..datasource import LiveDataError

DEFAULT_DATA_BASE = "https://quantapi.51ifind.com/api/v1"
# 技能包 get_history_quotes_indicators 示例中写明的行情指标。
CONFIRMED_HQ_INDICATORS = ("pre_close", "vwap", "open", "volume", "close")
# 日期序列示例里写明参数模板的指标。不是 ETF 份额，不能拿来顶替。
CONFIRMED_DS_INDICATOR = "ths_close_price_stock"

# 技能包没有给出数字，只要求按证券数 × 指标数 × 时间点分批。
# 2000 是保守默认，避免一次请求顶满未知上限；可用 NTM_IFIND_MAX_CELLS 覆盖。
ASSUMED_MAX_CELLS = 2000
# 运营方在本机实测的免费档，不是技能包里的固定数字（技能包要求以 get_limit_policy 为准）。
FREE_QPS_PER_FUNCTION = 10
FREE_QPS_TOTAL = 20

_REFRESH_KEYS = (
    "NTM_IFIND_REFRESH_TOKEN",
    "ifind_api_refresh_token",
    "IFIND_API_REFRESH_TOKEN",
)
_ACCESS_KEYS = (
    "ifind_api_access_token",
    "IFIND_API_ACCESS_TOKEN",
)
_SECRET_KEYS = _REFRESH_KEYS + _ACCESS_KEYS + ("NTM_IFIND_USER", "NTM_IFIND_PASS")


def data_base_url() -> str:
    return os.getenv("IFIND_DATA_API_BASE_URL", DEFAULT_DATA_BASE).strip().rstrip("/") or DEFAULT_DATA_BASE


def _configured(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value in {f"your {name}", "your refresh_token", "your access_token"}:
        return ""
    return value


def refresh_token_from_env() -> str:
    for name in _REFRESH_KEYS:
        value = _configured(name)
        if value:
            return value
    return ""


def access_token_from_env() -> str:
    for name in _ACCESS_KEYS:
        value = _configured(name)
        if value:
            return value
    return ""


def http_credentials_configured() -> bool:
    return bool(refresh_token_from_env() or access_token_from_env())


def scrub(text: str, extra: str = "") -> str:
    """从准备展示的文本里去掉环境中的令牌。"""
    out = text
    secrets = [extra] if extra else []
    secrets.extend(_configured(name) for name in _SECRET_KEYS)
    for secret in secrets:
        if secret and secret in out:
            out = out.replace(secret, "***")
    return out


def max_cells() -> int:
    raw = os.getenv("NTM_IFIND_MAX_CELLS", "").strip()
    if not raw:
        return ASSUMED_MAX_CELLS
    try:
        return max(1, int(raw))
    except ValueError:
        return ASSUMED_MAX_CELLS


def iter_chunks(codes: list[str], dates: list[str], n_indicators: int, limit: int) -> list[tuple[list[str], list[str]]]:
    """按单元格上限切开请求。单元格 = 证券数 × 指标数 × 日期数。"""
    if not codes or not dates or n_indicators < 1 or limit < 1:
        return []
    code_step = max(1, limit // n_indicators)
    chunks: list[tuple[list[str], list[str]]] = []
    for i in range(0, len(codes), code_step):
        code_batch = codes[i:i + code_step]
        width = max(1, len(code_batch) * n_indicators)
        date_step = max(1, limit // width)
        for j in range(0, len(dates), date_step):
            chunks.append((code_batch, dates[j:j + date_step]))
    return chunks


class Throttle:
    """滑动 1 秒窗口：每个函数路径不超过 per_function，全部请求不超过 total。"""

    def __init__(
        self,
        per_function: int = FREE_QPS_PER_FUNCTION,
        total: int = FREE_QPS_TOTAL,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        self.per_function = max(1, per_function)
        self.total = max(1, total)
        self._now = now or time.monotonic
        self._sleep = sleep or time.sleep
        self._by_fn: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()

    def wait(self, endpoint: str) -> None:
        while True:
            now = self._now()
            self._drop(self._all, now)
            bucket = self._by_fn.setdefault(endpoint, deque())
            self._drop(bucket, now)
            delay = 0.0
            if len(bucket) >= self.per_function:
                delay = max(delay, 1.0 - (now - bucket[0]))
            if len(self._all) >= self.total:
                delay = max(delay, 1.0 - (now - self._all[0]))
            if delay <= 0:
                stamp = self._now()
                bucket.append(stamp)
                self._all.append(stamp)
                return
            self._sleep(delay)

    @staticmethod
    def _drop(bucket: deque[float], now: float) -> None:
        while bucket and now - bucket[0] >= 1.0:
            bucket.popleft()


def _error_code(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_day(value: Any) -> str:
    text = str(value).strip().replace("/", "-")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


class IFindHttp:
    """只在内存里保存 access_token。repr 不包含令牌。"""

    def __init__(
        self,
        session: Any | None = None,
        throttle: Throttle | None = None,
        timeout: int = 30,
    ):
        self._session = session
        self.throttle = throttle or Throttle()
        self.timeout = timeout
        self._access = ""
        self._exchanged = False

    def __repr__(self) -> str:
        return "IFindHttp(token=redacted)"

    def _requests(self):
        if self._session is not None:
            return self._session
        try:
            import requests
        except ImportError as exc:
            raise LiveDataError("未安装 requests，无法使用 iFinD HTTP。") from exc
        return requests

    def ensure_access_token(self) -> None:
        if self._access:
            return
        env_token = access_token_from_env()
        if env_token:
            self._access = env_token
            self._exchanged = False
            return
        self._exchange()

    def _exchange(self) -> None:
        refresh = refresh_token_from_env()
        if not refresh:
            raise LiveDataError(
                "access_token 不可用，且未配置 NTM_IFIND_REFRESH_TOKEN"
                "（也接受 ifind_api_refresh_token）。已停止采集。"
            )
        payload = self._post(
            "get_access_token",
            {},
            access=False,
            refresh=refresh,
            allow_refresh=False,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        token = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise LiveDataError("令牌接口未返回 access_token，已停止采集。")
        self._access = token.strip()
        self._exchanged = True

    def invalidate(self) -> None:
        self._access = ""

    def _post(
        self,
        endpoint: str,
        body: dict[str, Any],
        *,
        access: bool = True,
        refresh: str = "",
        allow_refresh: bool = True,
    ) -> dict[str, Any]:
        self.throttle.wait(endpoint)
        if access:
            self.ensure_access_token()
        headers = {"Content-Type": "application/json"}
        if access:
            headers["access_token"] = self._access
        elif refresh:
            headers["refresh_token"] = refresh
        url = f"{data_base_url()}/{endpoint.lstrip('/')}"
        try:
            response = self._requests().post(url, json=body, headers=headers, timeout=self.timeout)
        except Exception as exc:
            # requests.RequestException 或其他网络错误。不链式抛出，避免请求头进入 traceback 文本。
            raise LiveDataError(f"iFinD HTTP 连接失败（{type(exc).__name__}），已停止采集") from None

        status = getattr(response, "status_code", 0)
        if status in (401, 403):
            if access and allow_refresh and refresh_token_from_env():
                self.invalidate()
                self._exchange()
                return self._post(endpoint, body, access=True, allow_refresh=False)
            raise LiveDataError(f"iFinD 鉴权失败（HTTP {status}），已停止采集")

        if not 200 <= status < 300:
            raise LiveDataError(f"iFinD HTTP {status}，已停止采集")
        try:
            payload = response.json()
        except ValueError:
            raise LiveDataError("iFinD 返回的不是 JSON，已停止采集") from None
        if not isinstance(payload, dict):
            raise LiveDataError("iFinD 返回的不是 JSON 对象，已停止采集")
        code = _error_code(payload.get("errorcode"))
        if code not in (None, 0):
            if code == -1302 and access and allow_refresh and refresh_token_from_env():
                self.invalidate()
                self._exchange()
                return self._post(endpoint, body, access=True, allow_refresh=False)
            if code == -1301:
                raise LiveDataError("iFinD refresh_token 无效（错误码 -1301），已停止采集")
            errmsg = scrub(str(payload.get("errmsg") or ""), self._access)
            raise LiveDataError(f"iFinD 错误码 {code}：{errmsg}。已停止采集")
        return payload

    def trade_dates(self, start: str, end: str) -> list[str]:
        """上交所交易日。tables 必须是文档中的对象，日期在 time。"""
        payload = self._post("get_trade_dates", {
            "marketcode": "212001",
            "startdate": start,
            "enddate": end,
            "functionpara": {
                "mode": "1",
                "dateType": "0",
                "period": "D",
                "dateFormat": "0",
            },
        })
        tables = payload.get("tables")
        if not isinstance(tables, dict) or not isinstance(tables.get("time"), list):
            raise LiveDataError("iFinD 交易日历响应缺少 tables.time，已停止采集")
        dates = []
        for item in tables["time"]:
            day = _normalize_day(item)
            if day and start <= day <= end:
                dates.append(day)
        if not dates:
            raise LiveDataError("iFinD 交易日历在该区间没有交易日，已停止采集")
        return dates

    def history_close(self, code: str, start: str, end: str) -> list[tuple[str, float]]:
        """THS_HQ / history_data。指标只用文档确认过的 close。"""
        if "close" not in CONFIRMED_HQ_INDICATORS:
            raise LiveDataError("close 不在已确认的历史行情指标中")
        limit = max_cells()
        # 单证券、单指标：按日期切。这里还没有交易日列表，按自然日窗口整段请求；
        # 单元格上限由调用方先按交易日切好再传入短区间。本方法再按 max_cells 兜底切自然日。
        from datetime import date, timedelta

        begin = date.fromisoformat(start)
        last = date.fromisoformat(end)
        spans: list[tuple[str, str]] = []
        cursor = begin
        while cursor <= last:
            chunk_end = min(last, cursor + timedelta(days=limit - 1))
            spans.append((cursor.isoformat(), chunk_end.isoformat()))
            cursor = chunk_end + timedelta(days=1)

        series: list[tuple[str, float]] = []
        for span_start, span_end in spans:
            payload = self._post("history_data", {
                "reqBody": {
                    "codes": code,
                    "indicators": "close",
                    "startdate": span_start,
                    "enddate": span_end,
                    "functionpara": {"Interval": "D", "Fill": "Blank"},
                },
            })
            series.extend(_series_from_tables(payload, code, "close"))
        series.sort(key=lambda item: item[0])
        dedup: dict[str, float] = {}
        for day, close in series:
            if start <= day <= end:
                dedup[day] = close
        ordered = sorted(dedup.items())
        if len(ordered) < 6:
            raise LiveDataError("iFinD 指数收盘价不足 6 个交易日，已停止采集")
        return ordered

    def date_sequence(
        self,
        codes: list[str],
        indicators: list[tuple[str, list[str]]],
        dates: list[str],
    ) -> dict[str, dict[str, dict[str, float]]]:
        """THS_DS。返回 {6位代码: {日期: {指标: 数值}}}。缺列就失败，不补空。"""
        if not dates:
            raise LiveDataError("iFinD 日期序列没有可查询的交易日")
        limit = max_cells()
        found: dict[str, dict[str, dict[str, float]]] = {}
        expected = {c.split(".")[0] for c in codes}
        names = [name for name, _ in indicators]
        for code_batch, date_batch in iter_chunks(codes, dates, len(indicators), limit):
            payload = self._post("date_sequence", {
                "codes": ",".join(code_batch),
                "startdate": date_batch[0],
                "enddate": date_batch[-1],
                "functionpara": {"Interval": "D", "Days": "Tradedays", "Fill": "Blank"},
                "indipara": [
                    {"indicator": name, "indiparams": params}
                    for name, params in indicators
                ],
            })
            for name in names:
                for raw_code, rows in _group_series(payload, name).items():
                    code = raw_code.split(".")[0]
                    bucket = found.setdefault(code, {})
                    for day, value in rows:
                        if day in date_batch:
                            bucket.setdefault(day, {})[name] = value
        missing = []
        for code in expected:
            for day in dates:
                row = found.get(code, {}).get(day, {})
                if any(name not in row for name in names):
                    missing.append(f"{code}@{day}")
        if missing:
            raise LiveDataError(
                "iFinD 日期序列缺少已请求的指标，拒绝用空值填充："
                + "、".join(missing[:8])
            )
        return found


def _series_from_tables(payload: dict[str, Any], code: str, indicator: str) -> list[tuple[str, float]]:
    grouped = _group_series(payload, indicator)
    rows = grouped.get(code) or grouped.get(code.split(".")[0])
    if not rows:
        raise LiveDataError(f"iFinD 响应没有 {code} 的 {indicator}，已停止采集")
    return rows


def _group_series(payload: dict[str, Any], indicator: str) -> dict[str, list[tuple[str, float]]]:
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        raise LiveDataError("iFinD tables 不是非空数组，已停止采集")
    grouped: dict[str, list[tuple[str, float]]] = {}
    for item in tables:
        if not isinstance(item, dict):
            raise LiveDataError("iFinD tables 项不是对象，已停止采集")
        raw_code = str(item.get("thscode") or "").strip()
        times = item.get("time")
        table = item.get("table")
        if not raw_code or not isinstance(times, list) or not isinstance(table, dict):
            raise LiveDataError("iFinD 响应缺少 thscode、time 或 table，已停止采集")
        values = None
        for key, val in table.items():
            if str(key).lower() == indicator.lower():
                values = val
                break
        if not isinstance(values, list):
            raise LiveDataError(f"iFinD table 缺少指标 {indicator}，已停止采集")
        if len(values) != len(times):
            raise LiveDataError(f"iFinD 指标 {indicator} 与时间轴长度不一致，已停止采集")
        rows: list[tuple[str, float]] = []
        for stamp, raw in zip(times, values):
            if raw is None or raw == "":
                continue
            try:
                number = float(raw)
            except (TypeError, ValueError):
                raise LiveDataError(f"iFinD 指标 {indicator} 含非数值，已停止采集") from None
            if number != number or abs(number) == float("inf"):
                raise LiveDataError(f"iFinD 指标 {indicator} 含无效数值，已停止采集")
            rows.append((_normalize_day(stamp), number))
        grouped[raw_code] = rows
    return grouped
