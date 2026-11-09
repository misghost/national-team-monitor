"""API 契约回归：字段、三层标注、未确权铁律、刷新。"""
from fastapi.testclient import TestClient

from ntm import db
from ntm.adapters.demo import DemoAdapter
from ntm.api import app


def _client_with_data() -> TestClient:
    db.init_db()
    if db.is_empty():
        db.save_dataset(DemoAdapter().fetch().dataset)
    return TestClient(app)


def test_health():
    with TestClient(app) as c:
        r = c.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": "1.0.0", "is_demo": True}


def test_overview_contract():
    with _client_with_data() as c:
        data = c.get("/api/overview").json()
    assert data["is_demo"] is True
    sig = data["signal"]
    assert sig["is_confirmed"] is False
    assert set(sig["conditions"]) == {"market_drop", "etf_sync", "official"}
    assert {"state", "state_label", "confidence", "confidence_label",
            "attribution", "summary", "caveat"} <= set(sig)
    assert {m["layer"] for m in data["layers"]} == {"L1", "L2", "L3"}
    l2 = next(m for m in data["layers"] if m["layer"] == "L2")
    assert l2["attributable"] is False
    assert "不构成投资建议" in data["disclaimer"]


def test_etf_series_is_marked_as_estimate():
    with _client_with_data() as c:
        items = c.get("/api/etf/shares?days=10").json()["items"]
    assert len(items) == 10
    assert all("不可归因" in i["note"] for i in items)
    assert all({"date", "index_close", "index_change_pct",
                "net_subscription_yi", "sync_etf_count"} <= set(i) for i in items)


def test_signals_never_confirmed():
    with _client_with_data() as c:
        items = c.get("/api/signals?days=90").json()["items"]
    assert items and all(i["is_confirmed"] is False for i in items)


def test_methodology_exposes_entities_traps_noise():
    with _client_with_data() as c:
        m = c.get("/api/methodology").json()
    assert len(m["glossary"]) >= 5
    assert len(m["traps"]) == 4
    assert len(m["noise"]) >= 5
    ids = {e["id"] for e in m["entities"]}
    assert "csfc" in ids and "huijin_asset" in ids


def test_annotations_and_holdings():
    with _client_with_data() as c:
        anns = c.get("/api/announcements?limit=3").json()["items"]
        holds = c.get("/api/holdings?limit=3").json()["items"]
    assert len(anns) == 3 and len(holds) == 3
    assert all(a["layer"] == "L3" for a in anns)
    assert all(h["layer"] == "L1" for h in holds)


def test_refresh_rebuilds_data():
    with _client_with_data() as c:
        r = c.post("/api/refresh")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_demo"] is True
    assert body["signals"] > 0
    assert body["signal_state"] is not None


def test_backtest_returns_pit_safe_result():
    with _client_with_data() as c:
        b = c.get("/api/backtest?horizon=20").json()
    assert "events" in b and "note" in b
    assert "样本量" in b["note"]
