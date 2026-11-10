"""Wind MCP 真实数据适配器（配置驱动，默认禁用）。

数据源：用户自行提供凭据的 Wind MCP Streamable HTTP 服务。
本模块只做三件事：按日期取数 → 转换成平台 dataset 契约 → 交给
``datasource.validate_dataset`` 校验；任何一步失败都抛 ``LiveDataError``
并停止采集，**绝不降级回演示数据、绝不用合成数据填充**。

取数口径（托管 MCP，2026-11-10 实测）：
- 端点 ``https://mcp.wind.com.cn/vserver_wind_tools/mcp/``。
  只有 ``market_search_tools`` 与 ``market_call_tool``。后者的 ``arguments``
  是 JSON 对象文本（tools/list 已核对）。
- L2 份额：``fund_get_size`` 的场内流通份额（份）、当期变化（份）、
  份额变动日期、上市基金规模（元）。样本见 510300.SH / 510050.SH。
  **不使用成交额或资金流顶替 total_share。**
- 净值与量能：``fund_get_listed_historical_price`` 的 IOPV（元）与成交额（元）。
- 指数序列：搜索样本里覆盖指数区间的工具是 ``securities_get_trend``。
  该工具的参数定义在附件里被截断，所以只有调用失败或本地没有参数定义时
  才回退 ``market_search_tools``。旧的 ``get_index_kline`` JSON 仍能解析，
  供已有测试使用。
- L3 官方公告：系统**不自动判定** is_supportive。仅从
  ``data/verified_announcements.json`` 读取人工核验后的记录；
  文件不存在即为空列表。这是防止「用新闻标题伪造确权」的刻意设计。
- L1 季度确权：Wind MCP 未提供十大流通股东明细工具，保持空列表并显式标注缺口。

历史累积：``db.save_dataset`` 是全量替换，若只取当日会清空历史序列、
导致 5 日跌幅与图表失效。因此每日快照存 ``data/wind_history.json``，
刷新时只补抓缺失交易日，再用完整历史组装 dataset。

铁律不变：L2 永远 attributable=False，所有信号 is_confirmed=False；
份额变化不等于国家队申购，接入真实数据不构成任何确权。
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

from .. import config
from ..datasource import LiveDataError, validate_dataset
from .base import BaseAdapter, FetchResult

SOURCE_NAME = "wind-mcp:fund_get_size+securities_get_trend+fund_get_listed_historical_price"
INDEX_CODE = "000300.SH"
INDEX_TOOL = "securities_get_trend"
HISTORY_FILENAME = "wind_history.json"
ANNOUNCEMENT_FILENAME = "verified_announcements.json"
# fund_get_listed_historical_price 的 windCodes.maxItems 在搜索样本里是 50。
# fund_get_size 的同字段上限在附件里被截断，先沿用 50，7 只标的一次就够。
CODE_BATCH = 50

SIZE_FIELDS = [
    "f_unit_floortrading", "f_unit_floortradingchange",
    "f_unit_changedate", "f_etf_netasset",
]
PRICE_FIELDS = ["f_dq_amount", "f_dq_close", "f_nav_iopv"]

# 中文列名用包含匹配，避免括号/空格差异导致解析失败
COL_CODE_KEYS = ("基金代码",)
COL_SHARE_KEYS = ("场内流通份额",)
COL_CHANGE_KEYS = ("场内流通份额变化",)
COL_CHANGEDATE_KEYS = ("基金份额变动日期",)
COL_SCALE_KEYS = ("基金规模合计",)
COL_AMOUNT_KEYS = ("成交额",)
COL_CLOSE_KEYS = ("收盘价",)
COL_IOPV_KEYS = ("IOPV",)


def _norm_code(wind_code: str) -> str:
    """Wind 代码 510300.SH -> 平台 6 位代码 510300。"""
    return str(wind_code).strip().split(".")[0]


def _wind_code(code: str) -> str:
    """平台 6 位代码 -> Wind 代码。5 开头为沪市，1 开头为深市。"""
    return code + (".SH" if code.startswith("5") else ".SZ")


def _num(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if s in ("", "--", "-", "None", "null", "nan"):
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if f == f and abs(f) != float("inf") else None  # 过滤 NaN / inf


def _col(header: list[str], *keywords: str) -> int | None:
    for i, h in enumerate(header):
        if all(k in h for k in keywords):
            return i
    return None


def parse_markdown_tables(text: str) -> list[dict[str, str]]:
    """解析 Wind 工具返回的 Markdown 表格，返回按表头列名索引的字典列表。"""
    tables: list[list[list[str]]] = []
    cur: list[list[str]] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cur.append([c.strip() for c in s.strip("|").split("|")])
        elif cur:
            tables.append(cur)
            cur = []
    if cur:
        tables.append(cur)

    rows: list[dict[str, str]] = []
    for table in tables:
        if len(table) < 2:
            continue
        header = table[0]
        if not any("基金代码" in h for h in header):
            continue
        body = table[1:]
        # 跳过 |---|---| 分隔行
        body = [r for r in body if not all(re.fullmatch(r":?-{2,}:?", c or "") for c in r)]
        for r in body:
            if len(r) != len(header):
                continue
            rows.append({h: r[i] for i, h in enumerate(header)})
    return rows


def _find(row: dict[str, str], *keywords: str) -> str | None:
    header, value = _find_cell(row, *keywords)
    return value if header else None


def _find_cell(row: dict[str, str], *keywords: str) -> tuple[str | None, str | None]:
    # 取列名最短的匹配项，避免「场内流通份额」误命中「当期场内流通份额变化」。
    hits = [(len(k), k, v) for k, v in row.items() if all(x in k for x in keywords)]
    if not hits:
        return None, None
    _, header, value = min(hits)
    return header, value


def _share_multiplier(header: str) -> float:
    """表头单位：份按原值，万份 ×10000，亿份 ×1e8。样本列是「（份）」。"""
    if "亿份" in header:
        return 1e8
    if "万份" in header:
        return 1e4
    return 1.0


def _yuan_to_yi(header: str, amount: float) -> float:
    """成交额或规模换成亿元。样本列单位是元。"""
    if "亿元" in header:
        return amount
    if "万元" in header:
        return amount / 1e4
    return amount / 1e8


def _chunks(items: list[str], size: int) -> list[list[str]]:
    step = max(1, size)
    return [items[i:i + step] for i in range(0, len(items), step)]


def parse_tool_search(text: str) -> list[dict[str, Any]]:
    """解析 market_search_tools 文本里的 JSON 行。"""
    try:
        payload = json.loads(text)
    except ValueError:
        raise LiveDataError("market_search_tools 返回的不是 JSON，已停止采集") from None
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise LiveDataError("market_search_tools 缺少 rows，已停止采集")
    out = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 5 or not isinstance(row[4], dict):
            continue
        out.append({
            "toolName": str(row[0]),
            "score": row[2],
            "description": str(row[3]),
            "params": row[4],
        })
    if not out:
        raise LiveDataError("market_search_tools 没有可用工具定义，已停止采集")
    return out


def select_index_schema(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """优先用搜索样本里点名的 securities_get_trend，否则取得分最高且提到指数的工具。"""
    for row in rows:
        if row["toolName"] == INDEX_TOOL:
            return row["params"]
    ranked = [row for row in rows if "指数" in row["description"]]
    if not ranked:
        raise LiveDataError("搜索结果里没有指数历史序列工具，已停止采集")
    ranked.sort(key=lambda row: float(row["score"] or 0), reverse=True)
    return ranked[0]["params"]


def arguments_from_schema(schema: dict[str, Any], code: str, begin: str, end: str) -> dict[str, Any]:
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict) or not properties:
        raise LiveDataError("指数工具参数定义为空，已停止采集")
    required = set(schema.get("required") or [])
    args: dict[str, Any] = {}
    for name, spec in properties.items():
        if not isinstance(spec, dict):
            continue
        lname = name.lower()
        kind = spec.get("type")
        desc = str(spec.get("description") or "")
        if kind == "array" and ("code" in lname or "代码" in desc):
            args[name] = [code]
        elif kind == "string" and "code" in lname:
            args[name] = code
        elif any(token in lname for token in ("start", "begin", "from")):
            args[name] = begin
        elif "end" in lname:
            args[name] = end
    missing = sorted(name for name in required if name not in args)
    if missing:
        raise LiveDataError("指数工具必填参数无法匹配：" + ",".join(missing))
    if not args:
        raise LiveDataError("指数工具参数无法从定义中对应到代码和日期，已停止采集")
    return args


def _markdown_dicts(text: str) -> list[dict[str, str]]:
    tables: list[list[list[str]]] = []
    cur: list[list[str]] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cur.append([c.strip() for c in s.strip("|").split("|")])
        elif cur:
            tables.append(cur)
            cur = []
    if cur:
        tables.append(cur)
    rows: list[dict[str, str]] = []
    for table in tables:
        if len(table) < 2:
            continue
        header = table[0]
        body = [r for r in table[1:] if not all(re.fullmatch(r":?-{2,}:?", c or "") for c in r)]
        for r in body:
            if len(r) != len(header):
                continue
            rows.append({h: r[i] for i, h in enumerate(header)})
    return rows


def _kline_points(text: str, as_of: str) -> list[dict[str, Any]]:
    """指数序列。优先识别旧版列式 JSON；否则按 Markdown 表的日期/收盘价列。"""
    stripped = (text or "").strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except ValueError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            if payload.get("error"):
                raise LiveDataError("指数序列返回错误，已停止采集")
            return _kline_points_from_columns(payload["data"], as_of)
    return _kline_points_from_markdown(stripped, as_of)


def _kline_points_from_columns(block: dict[str, Any], as_of: str) -> list[dict[str, Any]]:
    cols = [c.get("name", "") for c in block.get("columns", [])]
    rows = block.get("rows", [])
    if not rows or "TIME" not in cols or "MATCH" not in cols:
        raise LiveDataError("指数序列未返回 TIME/MATCH 列，已停止采集")
    i_time, i_close = cols.index("TIME"), cols.index("MATCH")
    i_turn = cols.index("TURNOVER") if "TURNOVER" in cols else None
    series = []
    for r in rows:
        day = str(r[i_time])[:10]
        if day > as_of:
            continue
        close = _num(r[i_close])
        if close is None or close <= 0:
            continue
        series.append({
            "date": day,
            "_close": close,
            "_amount": _num(r[i_turn]) if i_turn is not None else None,
        })
    return series


def _kline_points_from_markdown(text: str, as_of: str) -> list[dict[str, Any]]:
    rows = _markdown_dicts(text)
    if not rows:
        raise LiveDataError("指数序列没有可解析的 Markdown 表，已停止采集")
    header = list(rows[0])
    date_key = next((h for h in header if "日期" in h or h.upper() == "TIME"), None)
    close_key = next((h for h in header if "收盘" in h or h.upper() == "MATCH"), None)
    amount_key = next((h for h in header if "成交额" in h or h.upper() == "TURNOVER"), None)
    if not date_key or not close_key:
        raise LiveDataError(
            "指数 Markdown 表缺少日期或收盘价列（表头：" + "、".join(header) + "），已停止采集"
        )
    series = []
    for row in rows:
        day = str(row.get(date_key) or "")[:10]
        if not day or day > as_of:
            continue
        close = _num(row.get(close_key))
        if close is None or close <= 0:
            continue
        amount = _num(row.get(amount_key)) if amount_key else None
        if amount is not None and amount_key and "亿" in amount_key:
            amount *= 1e8
        elif amount is not None and amount_key and "万" in amount_key:
            amount *= 1e4
        series.append({"date": day, "_close": close, "_amount": amount})
    series.sort(key=lambda item: item["date"])
    return series


class WindMCPClient:
    """Wind MCP（stateless Streamable HTTP）最小 JSON-RPC 客户端。"""

    def __init__(self, endpoint: str, key: str, timeout: int, min_interval: float):
        self.endpoint = endpoint
        self.key = key
        self.timeout = timeout
        self.min_interval = min_interval
        self._last = 0.0
        self._id = 1000

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": "Bearer " + self.key,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            # 只暴露异常类型，绝不回显 URL / 请求头 / 凭据
            raise LiveDataError(f"Wind MCP 请求失败（{type(exc).__name__}），已停止采集") from None
        if resp.status_code != 200:
            raise LiveDataError(self._public(f"Wind MCP 返回 HTTP {resp.status_code}，已停止采集"))

        data = self._decode(resp.content)
        if "error" in data:
            code = (data["error"] or {}).get("code", "unknown")
            raise LiveDataError(f"Wind MCP 返回错误（code={code}），已停止采集")
        return data

    def _public(self, message: str) -> str:
        if self.key and self.key in message:
            return message.replace(self.key, "***")
        return message

    @staticmethod
    def _decode(content: bytes) -> dict[str, Any]:
        """兼容纯 JSON 与 SSE 两种响应。

        必须用 resp.content 自行按 UTF-8 解码：requests 对 text/event-stream
        默认按 ISO-8859-1 解码，中文会变成控制字符；再用 str.splitlines()
        会把 U+0085 等误判为换行，把一条 data: JSON 拦腰截断。
        """
        raw = content.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                if "error" in parsed:
                    code = (parsed["error"] or {}).get("code", "unknown")
                    raise LiveDataError(f"Wind MCP 返回错误（code={code}），已停止采集")
                return parsed
        except ValueError:
            pass

        chunks = []
        for line in raw.split("\n"):  # 只按 \n 切，避免误判 Unicode 行分隔符
            s = line.strip()
            if s.startswith("data:"):
                chunks.append(s[5:].strip())
        if not chunks:
            raise LiveDataError("Wind MCP SSE 响应中没有 data 段，已停止采集")
        candidates = list(reversed(chunks))
        if len(chunks) > 1:
            candidates.append("\n".join(chunks))  # SSE 规范：多 data 行用换行拼接
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                if "error" in parsed:
                    code = (parsed["error"] or {}).get("code", "unknown")
                    raise LiveDataError(f"Wind MCP 返回错误（code={code}），已停止采集")
                return parsed
        raise LiveDataError("Wind MCP 响应无法解析为 JSON 对象，已停止采集")

    def call(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """通过 market_call_tool 调用指定 Wind 工具，返回文本内容。"""
        self._id += 1
        self._throttle()
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "tools/call",
            "params": {
                "name": "market_call_tool",
                "arguments": {
                    "toolName": tool_name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            },
        }
        result = self._post(payload).get("result", {})
        if result.get("isError"):
            raise LiveDataError(f"Wind 工具 {tool_name} 返回错误，已停止采集")
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        text = "\n".join(texts).strip()
        if not text:
            raise LiveDataError(f"Wind 工具 {tool_name} 未返回数据，已停止采集")
        return self._public(text)

    def search_tools(self, question: str, top_k: int = 3) -> list[dict[str, Any]]:
        """market_search_tools。只在指数工具参数定义不完整时调用。"""
        self._id += 1
        self._throttle()
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "tools/call",
            "params": {
                "name": "market_search_tools",
                "arguments": {"question": question, "topK": top_k},
            },
        }
        result = self._post(payload).get("result", {})
        if result.get("isError"):
            raise LiveDataError("market_search_tools 返回错误，已停止采集")
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        return parse_tool_search(self._public("\n".join(texts)))


class WindMCPAdapter(BaseAdapter):
    """把 Wind MCP 的取数结果组装成平台 dataset，并强制通过校验闸门。"""

    name = "wind_mcp"
    verified = True

    def __init__(self, client: WindMCPClient | None = None, history_path: Path | None = None):
        self.client = client
        self.history_path = history_path or (config.DATA_DIR / HISTORY_FILENAME)
        self.announcement_path = config.DATA_DIR / ANNOUNCEMENT_FILENAME
        self.wind_codes = [_wind_code(code) for code, _ in config.CORE_ETF]
        self.pool = {code: name for code, name in config.CORE_ETF}
        self.backfill_days = config.wind_backfill_days()
        self.code_batch = CODE_BATCH
        self._index_schema: dict[str, Any] | None = None

    # ---------- 配置闸门 ----------
    def _build_client(self) -> WindMCPClient:
        if self.client is not None:
            return self.client
        key = config.wind_mcp_key()
        if not key:
            raise LiveDataError(
                "Wind MCP 未配置：需要 NTM_WIND_MCP_KEY。"
                "系统已停止采集，不会降级到演示数据。"
            )
        if not config.data_verified():
            raise LiveDataError(
                "NTM_DATA_VERIFIED=0：数据源核验清单未完成。已停止采集，不会降级到演示数据。"
            )
        return WindMCPClient(
            config.wind_mcp_endpoint(), key, config.WIND_MCP_TIMEOUT, config.WIND_MCP_MIN_INTERVAL
        )

    # ---------- 历史快照 ----------
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

    # ---------- 各层取数 ----------
    def _index_arguments(self, client: WindMCPClient, begin: str, end: str) -> dict[str, Any]:
        """securities_get_trend 的参数定义在附件中被截断，所以只在需要时搜索。"""
        if self._index_schema is None and hasattr(client, "search_tools"):
            rows = client.search_tools("沪深300指数 000300.SH 日收盘价历史序列", top_k=3)
            self._index_schema = select_index_schema(rows)
        if self._index_schema:
            return arguments_from_schema(self._index_schema, INDEX_CODE, begin, end)
        # 假客户端没有 search_tools。键名是假设，真实调用会走上面的搜索。
        return {"windCode": INDEX_CODE, "startDate": begin, "endDate": end}

    def _fetch_kline(self, client: WindMCPClient, as_of: str) -> list[dict[str, Any]]:
        end = date.fromisoformat(as_of)
        begin = (end - timedelta(days=config.wind_kline_days())).isoformat()
        text = client.call(INDEX_TOOL, self._index_arguments(client, begin, as_of))
        series = _kline_points(text, as_of)
        if len(series) < 6:
            raise LiveDataError("指数序列有效交易日不足 6 天，无法计算 5 日涨跌幅，已停止采集")

        closes = [r["_close"] for r in series]  # 先固化，避免下方 pop 影响前一日取值
        for i, row in enumerate(series):
            prev = closes[i - 1] if i >= 1 else closes[i]
            base5 = closes[i - 5] if i >= 5 else closes[0]
            amount_yi = row["_amount"] / 1e8 if row["_amount"] is not None else None
            row.update({
                "index_close": row["_close"],
                "index_change_pct": round((row["_close"] / prev - 1) * 100, 4),
                "index_change_5d_pct": round((row["_close"] / base5 - 1) * 100, 4),
                "amount_yi": round(amount_yi, 4) if amount_yi is not None else None,
            })
            row.pop("_close", None)
            row.pop("_amount", None)
        return series

    def _fetch_day(self, client: WindMCPClient, day: str) -> tuple[str, dict[str, dict[str, Any]]] | None:
        """取单个交易日的 7 只 ETF 份额与行情。返回 (真实数据日期, 快照)。"""
        size_rows: list[dict[str, str]] = []
        price_rows: list[dict[str, str]] = []
        for batch in _chunks(self.wind_codes, self.code_batch):
            size_text = client.call("fund_get_size", {
                "windCodes": batch,
                "asOfDate": day,
                "includeFields": SIZE_FIELDS,
            })
            price_text = client.call("fund_get_listed_historical_price", {
                "windCodes": batch,
                "tradeDate": day,
                "includeFields": PRICE_FIELDS,
            })
            parsed_size = parse_markdown_tables(size_text)
            if not parsed_size:
                raise LiveDataError(f"fund_get_size({day}) 未返回可解析表格，已停止采集")
            size_rows.extend(parsed_size)
            price_rows.extend(parse_markdown_tables(price_text))

        # ETF 份额是 T+1 数据：当日盘后尚未发布时，Wind 会把「基金份额变动日期」
        # 回退到上一交易日，且「当期场内流通份额变化」返回 "--"。这是正常状态，
        # 跳过该日即可 —— 绝不能把 11-09 的份额冒充成 11-10 的数据写入。
        changedates = [
            (_find(r, *COL_CHANGEDATE_KEYS) or "").strip()
            for r in size_rows if _find(r, *COL_CODE_KEYS)
        ]
        if changedates and all(d and d < day for d in changedates):
            return None

        prices: dict[str, dict[str, str]] = {}
        for row in price_rows:
            raw = _find(row, *COL_CODE_KEYS)
            if raw:
                prices[_norm_code(raw)] = row

        funds: dict[str, dict[str, Any]] = {}
        actual_date = ""
        for row in size_rows:
            raw_code = _find(row, *COL_CODE_KEYS)
            if not raw_code:
                continue
            code = _norm_code(raw_code)
            if code not in self.pool:
                continue
            share_header, share_raw = _find_cell(row, *COL_SHARE_KEYS)
            change_header, change_raw = _find_cell(row, *COL_CHANGE_KEYS)
            total_share = _num(share_raw)
            share_change = _num(change_raw)
            if total_share is not None and share_header:
                total_share *= _share_multiplier(share_header)
            if share_change is not None and change_header:
                share_change *= _share_multiplier(change_header)
            changedate = (_find(row, *COL_CHANGEDATE_KEYS) or "").strip()
            scale_header, scale_raw = _find_cell(row, "上市基金规模")
            if scale_raw is None:
                scale_header, scale_raw = _find_cell(row, *COL_SCALE_KEYS)
            scale = _num(scale_raw)
            price = prices.get(code, {})
            iopv = _num(_find(price, *COL_IOPV_KEYS))
            close = _num(_find(price, *COL_CLOSE_KEYS))
            amount_header, amount_raw = _find_cell(price, *COL_AMOUNT_KEYS)
            amount = _num(amount_raw)
            if scale is not None and scale_header:
                if "亿元" in scale_header:
                    scale *= 1e8
                elif "万元" in scale_header:
                    scale *= 1e4
            # nav 优先 IOPV（净值口径），其次收盘价，最后用 规模/份额 兜底
            nav = iopv or close or (scale / total_share if scale and total_share else None)
            if not (total_share and total_share > 0 and nav and nav > 0
                    and share_change is not None and changedate):
                raise LiveDataError(
                    f"fund_get_size({day}) 中 {code} 缺关键字段（份额/份额变化/净值/日期），已停止采集"
                )
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", changedate):
                raise LiveDataError(f"份额变动日期格式非法：{changedate}，已停止采集")
            if changedate != day:
                raise LiveDataError(
                    f"fund_get_size({day}) 返回的份额变动日期为 {changedate}，"
                    "各层数据日期不同步，已停止采集"
                )
            actual_date = max(actual_date, changedate)
            funds[code] = {
                "total_share": total_share,
                "share_change": share_change,
                "nav": nav,
                "amount_yi": round(_yuan_to_yi(amount_header or "元", amount), 4) if amount is not None else 0.0,
                "est_net_yi": round(share_change * nav / 1e8, 4),
            }
        if not funds or not actual_date:
            raise LiveDataError(f"fund_get_size({day}) 未返回标的池内有效数据，已停止采集")
        return actual_date, funds

    def _verified_announcements(self) -> list[dict[str, Any]]:
        """L3 只接受人工核验录入，系统不自动判定 is_supportive。"""
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
            out.append({
                "date": str(item["date"]), "source": str(item["source"]),
                "title": str(item["title"]), "type": str(item["type"]),
                "is_supportive": bool(item["is_supportive"]),
                "url": str(item.get("url", "")), "layer": "L3",
            })
        return out

    # ---------- 主流程 ----------
    def fetch(self, as_of: str) -> FetchResult:
        client = self._build_client()
        try:
            date.fromisoformat(as_of)
        except (TypeError, ValueError):
            raise LiveDataError(f"as_of 不是合法 ISO 日期：{as_of}") from None

        market = self._fetch_kline(client, as_of)
        trade_days = [r["date"] for r in market]
        history = self._load_history()
        days = history["days"]

        targets = trade_days[-self.backfill_days:]
        missing = [d for d in targets if d not in days]
        for d in missing:
            got = self._fetch_day(client, d)
            if got:
                actual, funds = got
                if actual <= as_of:  # 防前视
                    days.setdefault(actual, {}).update(funds)
        self._save_history(history)

        # 各层日期对齐：市场行情取到份额最新日为止，避免「今天盘中指数 + 昨天份额」
        # 混算信号。ETF 份额为 T+1，全站以最新完整交易日为口径。
        latest = max(days) if days else ""
        if latest:
            market = [r for r in market if r["date"] <= latest]
        if not market:
            raise LiveDataError("行情序列与份额数据日期无交集，已停止采集")

        etf: list[dict[str, Any]] = []
        for day in sorted(days):
            if day > as_of:
                continue
            for code, snap in sorted(days[day].items()):
                if code not in self.pool:
                    continue
                etf.append({
                    "date": day, "code": code, "name": self.pool[code],
                    "total_share": snap["total_share"],
                    "share_change": snap["share_change"],
                    "nav": snap["nav"],
                    "est_net_yi": snap["est_net_yi"],
                    "amount_yi": snap.get("amount_yi", 0.0),
                    "layer": "L2", "attributable": False,
                    "source": "wind-mcp:fund_get_size(场内流通份额)",
                })
        if not etf:
            raise LiveDataError("Wind MCP 未取到任何 ETF 份额数据，已停止采集")

        dataset: dict[str, Any] = {
            "market": market,
            "etf": etf,
            "announcements": [a for a in self._verified_announcements() if a["date"] <= as_of],
            "holdings": [],
            "source": SOURCE_NAME,
            "is_demo": False,
            "as_of": as_of,
            "data_latest_date": latest,
        }
        problems = validate_dataset(dataset, as_of)
        if problems:
            raise LiveDataError(
                "Wind 真实数据质量校验未通过（已丢弃本次采集，不入库、不降级）："
                + "；".join(problems[:12])
            )

        warnings = [
            "真实数据已接入，但 L2 仍不可归因：无法识别 ETF 申购人身份",
            "所有信号 is_confirmed 恒为 False，本系统不提供买卖建议",
            "L1 季度十大流通股东未接入（Wind MCP 无对应明细工具），相关结论不可用",
            "L3 官方公告仅接受人工核验录入，系统不自动判定，三重共振不会因新闻误触发",
            "Wind 数据源的授权、版权与频控责任由接入方承担",
        ]
        return FetchResult(dataset=dataset, source=SOURCE_NAME,
                           is_demo=False, verified=True, warnings=warnings)


__all__ = ["WindMCPAdapter", "WindMCPClient", "parse_markdown_tables", "SOURCE_NAME"]
