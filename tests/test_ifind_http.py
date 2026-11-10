"""iFinD HTTP 通道：用文档形状的假响应，不访问 quantapi.51ifind.com。"""
from __future__ import annotations

import json

import pytest

from ntm import config
from ntm.adapters.ifind import IFindAdapter
from ntm.adapters.ifind_http import (
    IFindHttp,
    Throttle,
    iter_chunks,
    scrub,
)
from ntm.datasource import LiveDataError, validate_dataset

SECRET = "refresh-token-should-not-leak"
ACCESS = "access-token-should-not-leak"
AS_OF = "2026-11-10"
DAYS = [
    "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05",
    "2026-11-06", "2026-11-09", "2026-11-10",
]


class Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class Session:
    def __init__(self, auth_failures=0):
        self.calls = []
        self.auth_failures = auth_failures

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": dict(headers or {})})
        if url.endswith("/get_access_token"):
            if headers.get("refresh_token") == "bad-refresh":
                return Resp({"errorcode": -1301, "errmsg": "bad refresh"})
            return Resp({"errorcode": 0, "errmsg": "", "data": {"access_token": ACCESS}})
        if url.endswith("/history_data") and self.auth_failures:
            self.auth_failures -= 1
            return Resp({"errorcode": -1302, "errmsg": "access expired"})
        if url.endswith("/get_trade_dates"):
            return Resp({"errorcode": 0, "tables": {"time": DAYS}})
        if url.endswith("/history_data"):
            body = json["reqBody"]
            return Resp(_history(body["startdate"], body["enddate"]))
        if url.endswith("/date_sequence"):
            return Resp(_sequence(json["codes"], json["startdate"], json["enddate"], json["indipara"]))
        raise AssertionError(url)


def _history(start, end):
    times, values = [], []
    for i, day in enumerate(DAYS):
        if start <= day <= end:
            times.append(day)
            values.append(3900 + i * 10)
    return {"errorcode": 0, "tables": [{
        "thscode": "000300.SH",
        "time": times,
        "table": {"close": values},
    }]}


def _sequence(codes, start, end, indipara):
    names = [item["indicator"] for item in indipara]
    tables = []
    for code in codes.split(","):
        times, columns = [], {name: [] for name in names}
        for i, day in enumerate(DAYS):
            if start <= day <= end and day in DAYS:
                times.append(day)
                columns[names[0]].append(20_000_000_000 + i * 1_000_000)
                if len(names) > 1:
                    columns[names[1]].append(1.25)
        tables.append({"thscode": code, "time": times, "table": columns})
    return {"errorcode": 0, "tables": tables}


def _client(monkeypatch, **kwargs):
    monkeypatch.setenv("NTM_IFIND_REFRESH_TOKEN", SECRET)
    monkeypatch.delenv("ifind_api_access_token", raising=False)
    monkeypatch.delenv("IFIND_API_ACCESS_TOKEN", raising=False)
    session = kwargs.pop("session", None) or Session()
    throttle = Throttle(per_function=1000, total=1000, sleep=lambda _s: None)
    return IFindHttp(session=session, throttle=throttle), session


def test_chunks_respect_cell_budget():
    codes = [f"c{i}" for i in range(5)]
    dates = [f"2026-11-{i:02d}" for i in range(1, 11)]
    chunks = iter_chunks(codes, dates, n_indicators=2, limit=8)
    assert chunks
    assert all(len(c) * 2 * len(d) <= 8 for c, d in chunks)
    assert {code for batch, _ in chunks for code in batch} == set(codes)


def test_throttle_spaces_calls():
    clock = {"t": 0.0}
    slept = []

    def now():
        return clock["t"]

    def sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds

    limiter = Throttle(per_function=2, total=3, now=now, sleep=sleep)
    limiter.wait("history_data")
    limiter.wait("history_data")
    limiter.wait("history_data")
    assert slept and slept[0] > 0


def test_token_exchange_is_cached_and_history_uses_req_body(monkeypatch):
    client, session = _client(monkeypatch)
    rows = client.history_close("000300.SH", DAYS[0], DAYS[-1])
    assert rows[0] == (DAYS[0], 3900)
    assert sum(1 for call in session.calls if call["url"].endswith("/get_access_token")) == 1
    history = next(call for call in session.calls if call["url"].endswith("/history_data"))
    assert "reqBody" in history["json"]
    assert history["json"]["reqBody"]["indicators"] == "close"
    assert history["headers"]["access_token"] == ACCESS
    assert SECRET not in json.dumps(history["json"])
    assert "token=redacted" in repr(client)


def test_auth_error_refreshes_once(monkeypatch):
    session = Session(auth_failures=1)
    client, _ = _client(monkeypatch, session=session)
    client.history_close("000300.SH", DAYS[0], DAYS[-1])
    assert sum(1 for call in session.calls if call["url"].endswith("/get_access_token")) == 2


def test_bad_refresh_token_does_not_loop_or_leak(monkeypatch):
    monkeypatch.setenv("NTM_IFIND_REFRESH_TOKEN", "bad-refresh")
    session = Session()
    client = IFindHttp(session=session, throttle=Throttle(sleep=lambda _s: None))
    with pytest.raises(LiveDataError) as exc:
        client.history_close("000300.SH", DAYS[0], DAYS[-1])
    assert "-1301" in str(exc.value)
    assert "bad-refresh" not in str(exc.value)
    assert sum(1 for call in session.calls if call["url"].endswith("/get_access_token")) == 1


def test_verified_ifind_fields_default_without_http(monkeypatch):
    from ntm.adapters.ifind import VERIFIED_NAV_FIELD, VERIFIED_SHARE_FIELD

    monkeypatch.delenv("NTM_IFIND_SHARE_FIELD", raising=False)
    monkeypatch.delenv("NTM_IFIND_SHARE_PARAMS", raising=False)
    monkeypatch.delenv("NTM_IFIND_NAV_FIELD", raising=False)
    monkeypatch.delenv("NTM_IFIND_NAV_PARAMS", raising=False)
    adapter = IFindAdapter()
    assert adapter._indicator_spec(
        "NTM_IFIND_SHARE_FIELD", "NTM_IFIND_SHARE_PARAMS",
        VERIFIED_SHARE_FIELD, "missing",
    ) == ("ths_inner_float_shares_fund", [])
    assert adapter._indicator_spec(
        "NTM_IFIND_NAV_FIELD", "NTM_IFIND_NAV_PARAMS",
        VERIFIED_NAV_FIELD, "missing",
    ) == ("ths_iopv_fund", [])


def test_custom_share_field_without_params_stops(monkeypatch):
    monkeypatch.setenv("NTM_DATA_VERIFIED", "1")
    monkeypatch.setenv("NTM_IFIND_REFRESH_TOKEN", SECRET)
    monkeypatch.setenv("NTM_IFIND_SHARE_FIELD", "custom_share_field")
    monkeypatch.delenv("NTM_IFIND_SHARE_PARAMS", raising=False)
    with pytest.raises(LiveDataError) as exc:
        IFindAdapter().fetch(AS_OF)
    assert "NTM_IFIND_SHARE_PARAMS" in str(exc.value)
    assert SECRET not in str(exc.value)


def test_http_fetch_passes_validation_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("NTM_DATA_VERIFIED", "1")
    monkeypatch.setenv("NTM_IFIND_REFRESH_TOKEN", SECRET)
    monkeypatch.setenv("NTM_IFIND_SHARE_FIELD", "ths_share_unconfirmed")
    monkeypatch.setenv("NTM_IFIND_SHARE_PARAMS", "[]")
    monkeypatch.setenv("NTM_IFIND_NAV_FIELD", "ths_nav_unconfirmed")
    monkeypatch.setenv("NTM_IFIND_NAV_PARAMS", "[]")
    monkeypatch.setattr(config, "wind_kline_days", lambda: 20)
    monkeypatch.setattr(config, "wind_backfill_days", lambda: 4)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    session = Session()
    http = IFindHttp(session=session, throttle=Throttle(per_function=1000, total=1000, sleep=lambda _s: None))
    adapter = IFindAdapter(history_path=tmp_path / "ifind_history.json", http=http)
    result = adapter.fetch(AS_OF)
    assert result.is_demo is False
    assert validate_dataset(result.dataset, AS_OF) == []
    assert result.dataset["market"][-1]["date"] == AS_OF
    assert all(row["attributable"] is False and row["layer"] == "L2" for row in result.dataset["etf"])
    blob = (tmp_path / "ifind_history.json").read_text(encoding="utf-8")
    assert SECRET not in blob and ACCESS not in blob
    assert scrub(f"see {SECRET}") == "see ***"


def test_broken_table_does_not_become_empty_success(monkeypatch):
    monkeypatch.setenv("NTM_IFIND_REFRESH_TOKEN", SECRET)

    class Empty(Session):
        def post(self, url, json=None, headers=None, timeout=None):
            self.calls.append(url)
            if url.endswith("/get_access_token"):
                return Resp({"errorcode": 0, "data": {"access_token": ACCESS}})
            return Resp({"errorcode": 0, "tables": [{"thscode": "000300.SH", "time": DAYS, "table": {}}]})

    client = IFindHttp(session=Empty(), throttle=Throttle(sleep=lambda _s: None))
    with pytest.raises(LiveDataError) as exc:
        client.history_close("000300.SH", DAYS[0], DAYS[-1])
    assert "close" in str(exc.value)
    assert ACCESS not in str(exc.value)
