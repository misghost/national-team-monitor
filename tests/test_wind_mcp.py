"""Wind MCP 适配器单测：全部使用本地假客户端，不发起任何真实网络请求。"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from ntm import config
from ntm.adapters import wind_mcp as wm
from ntm.datasource import LiveDataError, validate_dataset

POOL_CODES = [code for code, _ in config.CORE_ETF]
AS_OF = "2026-11-10"
DAYS = [(date.fromisoformat(AS_OF) - timedelta(days=i)).isoformat()
        for i in range(11, -1, -1)]  # 12 个交易日，升序


def kline_text(days=DAYS, base=4000.0) -> str:
    rows = []
    for i, d in enumerate(days):
        close = base + i * 10
        rows.append([
            f"{d}T00:00:00.000+08:00", str(close - 5), str(close),
            str(close + 20), str(close - 20),
            str(400000000000 + i * 1_000_000_000), "1000000", "0.5", str(close),
        ])
    return json.dumps({
        "data": {
            "columns": [{"name": n} for n in (
                "TIME", "OPEN", "MATCH", "HIGH", "LOW", "TURNOVER",
                "VOLUME", "CHANGEHANDRATE", "AVPRICE")],
            "rows": rows,
        },
        "error": None,
    }, ensure_ascii=False)


def size_text(day: str) -> str:
    head = ("| 序号 | 基金代码 | 基金简称 | 实际报告期 | 场内流通份额（份） | "
            "当期场内流通份额变化（份） | 基金份额变动日期 | 基金规模合计（元） |")
    sep = "|---|---|---|---|---|---|---|---|"
    lines = ["## 查询结果", "", "### 数据结果", head, sep]
    for i, code in enumerate(POOL_CODES, 1):
        wc = wm._wind_code(code)
        share = 10_000_000_000 + i * 1_000_000_000
        change = 50_000_000 * (1 if i % 2 else -1)
        lines.append(
            f"| {i} | {wc} | ETF{i} | 2026-09-30 | {share} | {change} | {day} | {share * 2.5} |"
        )
    return "\n".join(lines)


def price_text() -> str:
    head = "| 序号 | 基金代码 | 基金简称 | 成交额（元） | 收盘价（元） | IOPV（元） |"
    sep = "|---|---|---|---|---|---|"
    lines = [head, sep]
    for i, code in enumerate(POOL_CODES, 1):
        lines.append(
            f"| {i} | {wm._wind_code(code)} | ETF{i} | {3_000_000_000 + i * 100_000_000} "
            f"| {2.0 + i * 0.1:.3f} | {2.0 + i * 0.1 + 0.0003:.4f} |"
        )
    return "\n".join(lines)


class FakeClient:
    def __init__(self, fail_on=None, size_day_override=None):
        self.calls: list[tuple[str, dict]] = []
        self.fail_on = fail_on or set()
        self.size_day_override = size_day_override

    def call(self, tool_name: str, arguments: dict) -> str:
        self.calls.append((tool_name, arguments))
        if tool_name in self.fail_on:
            raise LiveDataError("模拟 Wind 工具失败")
        if tool_name == "get_index_kline":
            return kline_text()
        if tool_name == "fund_get_size":
            return size_text(self.size_day_override or arguments["asOfDate"])
        if tool_name == "fund_get_listed_historical_price":
            return price_text()
        raise AssertionError("未知工具 " + tool_name)


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return wm.WindMCPAdapter(client=FakeClient(), history_path=tmp_path / "hist.json")


def test_parse_markdown_tables():
    rows = wm.parse_markdown_tables(size_text(AS_OF))
    assert len(rows) == len(POOL_CODES)
    assert wm._norm_code(wm._find(rows[0], "基金代码")) == POOL_CODES[0]
    # 关键：份额与份额变化不能互相串列
    assert wm._num(wm._find(rows[0], "场内流通份额")) == 11_000_000_000
    assert wm._num(wm._find(rows[0], "场内流通份额变化")) == 50_000_000


def test_num_handles_missing_and_formatting():
    assert wm._num("--") is None
    assert wm._num("") is None
    assert wm._num("1,234.5") == 1234.5
    assert wm._num("nan") is None
    assert wm._num(float("inf")) is None


def test_kline_computes_changes_and_blocks_lookahead(adapter):
    market = adapter._fetch_kline(adapter.client, AS_OF)
    assert len(market) == len(DAYS)
    assert all(r["date"] <= AS_OF for r in market)
    first = market[0]
    assert first["index_change_pct"] == 0.0  # 无前一日时以自身为基准
    assert pytest.approx(market[1]["index_change_pct"], abs=1e-3) == 10 / 4000 * 100
    assert pytest.approx(market[5]["index_change_5d_pct"], abs=1e-3) == 50 / 4000 * 100
    assert market[-1]["amount_yi"] > 0


def test_fetch_day_uses_share_not_turnover(adapter):
    day, funds = adapter._fetch_day(adapter.client, AS_OF)
    assert day == AS_OF
    assert set(funds) == set(POOL_CODES)
    one = funds[POOL_CODES[0]]
    assert one["total_share"] == 11_000_000_000          # 份额（份）
    assert one["amount_yi"] == pytest.approx(31.0)       # 成交额（亿元），与份额完全不同量级
    assert one["total_share"] != one["amount_yi"]
    assert one["est_net_yi"] == pytest.approx(round(one["share_change"] * one["nav"] / 1e8, 4))


def test_full_fetch_passes_validation_gate(adapter):
    result = adapter.fetch(AS_OF)
    assert result.is_demo is False and result.verified is True
    assert validate_dataset(result.dataset, AS_OF) == []
    etf = result.dataset["etf"]
    expected = len(POOL_CODES) * min(len(DAYS), config.wind_backfill_days())
    assert len(etf) == expected
    assert all(r["layer"] == "L2" and r["attributable"] is False for r in etf)
    assert all("demo" not in r["source"].lower() for r in etf)
    assert result.dataset["holdings"] == []          # L1 缺口显式留空
    assert result.dataset["announcements"] == []     # L3 不自动确权
    assert result.dataset["market"]


def test_history_reuses_cached_days(adapter):
    adapter.fetch(AS_OF)
    first_calls = len(adapter.client.calls)
    adapter.client.calls.clear()
    adapter.fetch(AS_OF)
    # 第二次只保留 K 线，已缓存的交易日不再重复抓取
    assert len(adapter.client.calls) == 1
    assert adapter.client.calls[0][0] == "get_index_kline"
    assert first_calls > 1


def test_failure_stops_without_demo_or_partial_write(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    client = FakeClient(fail_on={"fund_get_size"})
    path = tmp_path / "hist.json"
    ad = wm.WindMCPAdapter(client=client, history_path=path)
    with pytest.raises(LiveDataError):
        ad.fetch(AS_OF)
    assert not path.exists()  # 不落半份数据


def test_verified_announcements_are_manual_only(adapter, tmp_path):
    assert adapter._verified_announcements() == []
    (tmp_path / wm.ANNOUNCEMENT_FILENAME).write_text(json.dumps([
        {"date": "2026-11-09", "source": "中央汇金", "title": "增持公告",
         "type": "增持公告", "is_supportive": True, "url": ""},
    ]), encoding="utf-8")
    anns = adapter._verified_announcements()
    assert len(anns) == 1 and anns[0]["is_supportive"] is True
    assert anns[0]["layer"] == "L3"
    result = adapter.fetch(AS_OF)
    assert len(result.dataset["announcements"]) == 1


def test_missing_key_field_stops(adapter):
    class BadClient(FakeClient):
        def call(self, tool_name, arguments):
            if tool_name == "fund_get_size":
                return size_text(AS_OF).replace("| 50000000 |", "| -- |", 1)
            return super().call(tool_name, arguments)

    adapter.client = BadClient()
    with pytest.raises(LiveDataError):
        adapter.fetch(AS_OF)


def size_text_unpublished(fallback: str) -> str:
    """当日份额尚未发布：份额日期回退到上一交易日，当日份额变化为 --。"""
    head = ("| 序号 | 基金代码 | 基金简称 | 实际报告期 | 场内流通份额（份） | "
            "当期场内流通份额变化（份） | 基金份额变动日期 | 基金规模合计（元） |")
    lines = [head, "|---|---|---|---|---|---|---|---|"]
    for i, code in enumerate(POOL_CODES, 1):
        share = 10_000_000_000 + i * 1_000_000_000
        lines.append(f"| {i} | {wm._wind_code(code)} | ETF{i} | 2026-09-30 | "
                     f"{share} | -- | {fallback} | {share * 2.5} |")
    return "\n".join(lines)


def test_unpublished_day_skipped_and_layers_aligned(tmp_path, monkeypatch):
    """T+1 份额未发布时跳过当日，且不得拿旧数据冒充、不得混算日期。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    fallback = DAYS[-2]

    class TPlusOneClient(FakeClient):
        def call(self, tool_name, arguments):
            if tool_name == "fund_get_size" and arguments["asOfDate"] == AS_OF:
                return size_text_unpublished(fallback)
            return super().call(tool_name, arguments)

    ad = wm.WindMCPAdapter(client=TPlusOneClient(), history_path=tmp_path / "h.json")
    result = ad.fetch(AS_OF)
    assert result.dataset["data_latest_date"] == fallback
    assert AS_OF not in [r["date"] for r in result.dataset["etf"]]
    assert all(r["date"] <= fallback for r in result.dataset["market"])
    assert all(r["date"] <= fallback for r in result.dataset["etf"])
    assert validate_dataset(result.dataset, AS_OF) == []


def test_decode_sse_with_utf8_chinese():
    """回归：SSE 含中文时不得被 splitlines() 误切断。"""
    payload = {"jsonrpc": "2.0", "id": 7,
               "result": {"content": [{"type": "text", "text": "沪深300ETF 中文内容"}]}}
    raw = "event: message\r\ndata: " + json.dumps(payload, ensure_ascii=False) + "\r\n\r\n"
    assert wm.WindMCPClient._decode(raw.encode("utf-8")) == payload


def test_decode_plain_json_and_rpc_error():
    assert wm.WindMCPClient._decode(b'{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}')["result"] == {"tools": []}
    with pytest.raises(LiveDataError):
        wm.WindMCPClient._decode(b'{"jsonrpc":"2.0","id":1,"error":{"code":-32601}}')


def test_wind_code_mapping():
    assert wm._wind_code("510300") == "510300.SH"
    assert wm._wind_code("159915") == "159915.SZ"
    assert wm._norm_code("588000.SH") == "588000"
