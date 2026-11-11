"""宽基长趋势：官方净申购与行情文件按日期合并，缺文件时不坏。"""
from fastapi.testclient import TestClient

from ntm.adapters.demo import DemoAdapter
from ntm.api import app
from ntm.etf_history import SHORT_NAMES, build_history
from tests.test_api import _client_with_data


def test_merge_keeps_net_null_when_only_quote_exists():
    official = [{
        "date": "2026-11-10",
        "code": "510300",
        "name": "沪深300ETF华泰柏瑞（华泰柏瑞沪深300ETF）",
        "est_net_yi": -13.4718,
        "amount_yi": 49.8324,
        "nav": 4.135,
    }]
    kline = {
        "as_of": "2026-11-10",
        "source": "公开行情",
        "items": [
            {
                "code": "510300",
                "name": "长名不应出现",
                "points": [
                    {"date": "2026-11-09", "close": 4.1, "amount_yi": 80},
                    {"date": "2026-11-10", "close": 9.9, "amount_yi": 1},
                    {"date": "2026-11-11", "close": 5, "amount_yi": 5},
                ],
            },
            {
                "code": "999999",
                "points": [{"date": "2026-11-10", "close": 1, "amount_yi": 1}],
            },
        ],
    }
    out = build_history(official, kline, days=250)
    assert out["as_of"] == "2026-11-10"
    assert [item["code"] for item in out["items"]] == list(SHORT_NAMES)
    item = next(row for row in out["items"] if row["code"] == "510300")
    assert item["name"] == "华泰柏瑞沪深300"
    assert [point["date"] for point in item["points"]] == ["2026-11-09", "2026-11-10"]
    early, late = item["points"]
    assert early["net_yi"] is None
    assert early["amount_yi"] == 80
    assert early["close"] == 4.1
    assert late["net_yi"] == -13.4718
    assert late["amount_yi"] == 49.8324
    assert late["close"] == 4.135
    assert all(row["code"] != "999999" for row in out["items"])
    others = [row for row in out["items"] if row["code"] != "510300"]
    assert all(row["points"] == [] for row in others)


def test_merge_days_cuts_backward_from_as_of():
    official = [
        {"date": "2026-11-09", "code": "510050", "est_net_yi": 1, "amount_yi": 10, "nav": 2},
        {"date": "2026-11-10", "code": "510050", "est_net_yi": 2, "amount_yi": 11, "nav": 2.1},
    ]
    kline = {
        "as_of": "2026-11-10",
        "items": [{
            "code": "510050",
            "points": [
                {"date": "2026-11-06", "close": 1, "amount_yi": 8},
                {"date": "2026-11-09", "close": 9, "amount_yi": 99},
            ],
        }],
    }
    out = build_history(official, kline, days=2)
    item = next(row for row in out["items"] if row["code"] == "510050")
    assert [point["date"] for point in item["points"]] == ["2026-11-09", "2026-11-10"]
    assert item["points"][0]["amount_yi"] == 10
    assert item["points"][0]["net_yi"] == 1
    assert out["as_of"] == "2026-11-10"


def test_history_without_kline_uses_official_rows_only():
    with _client_with_data() as client:
        body = client.get("/api/etf/history").json()
    assert body["is_demo"] is True
    assert body["as_of"]
    assert [item["code"] for item in body["items"]] == list(SHORT_NAMES)
    assert [item["name"] for item in body["items"]] == list(SHORT_NAMES.values())
    for item in body["items"]:
        assert item["points"]
        assert all(point["net_yi"] is not None for point in item["points"])
        assert all({"date", "net_yi", "amount_yi", "close"} <= set(point) for point in item["points"])


def test_history_bad_kline_file_falls_back(tmp_path):
    (tmp_path / "etf_kline.json").write_text("{", encoding="utf-8")
    with _client_with_data() as client:
        body = client.get("/api/etf/history?days=5").json()
    assert body["is_demo"] is True
    assert len(body["items"][0]["points"]) == 5
    assert all(point["net_yi"] is not None for point in body["items"][0]["points"])


def test_history_endpoint_merges_file(tmp_path):
    db_ready = DemoAdapter().fetch().dataset
    from ntm import db
    db.init_db()
    db.save_dataset(db_ready)
    (tmp_path / "etf_kline.json").write_text(
        '{"as_of":"2026-11-09","items":[{"code":"510300","points":['
        '{"date":"2024-01-02","close":3.5,"amount_yi":40},'
        '{"date":"2026-12-01","close":9,"amount_yi":9}]}]}',
        encoding="utf-8",
    )
    with TestClient(app) as client:
        body = client.get("/api/etf/history?days=250").json()
    item = next(row for row in body["items"] if row["code"] == "510300")
    assert item["points"][0]["date"] == "2024-01-02"
    assert item["points"][0]["net_yi"] is None
    assert item["points"][0]["amount_yi"] == 40
    assert all(point["date"] <= "2026-11-09" for point in item["points"])
    assert any(point["net_yi"] is not None for point in item["points"])


def test_history_rejects_non_positive_days():
    with _client_with_data() as client:
        assert client.get("/api/etf/history?days=0").status_code == 422
