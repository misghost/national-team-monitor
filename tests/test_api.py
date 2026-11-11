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


def test_etf_pool_contract():
    with _client_with_data() as c:
        d = c.get("/api/etf/pool").json()
    assert len(d["items"]) == 7
    assert d["validation"] == []
    assert d["is_demo"] is True
    assert all(cl["verified"] is False for cl in d["claims"])
    c159845 = next(x for x in d["items"] if x["code"] == "159845")
    assert "华夏" in c159845["name"] and "易方达" not in c159845["name"]


def test_etf_momentum_contract_and_neutrality():
    with _client_with_data() as c:
        d = c.get("/api/etf/momentum").json()
    assert d["is_demo"] is True
    assert len(d["items"]) == 7
    assert d["summary"]["is_signal"] is False
    required = {"code", "name", "amount_yi", "amount_prev_yi", "amount_ma_yi",
                "amount_change_yi", "amount_change_pct", "volume_multiple",
                "is_spike", "is_2026_focus", "share_change_yi", "note"}
    assert all(required <= set(i) for i in d["items"])
    assert "不能推出" in d["caveat"] or "不等于" in d["caveat"]


def test_momentum_does_not_enter_signal_conditions():
    """量能观测是独立辅助层，不得污染三重共振条件集合。"""
    with _client_with_data() as c:
        sig = c.get("/api/overview").json()["signal"]
        mom = c.get("/api/etf/momentum").json()
    assert set(sig["conditions"]) == {"market_drop", "etf_sync", "official"}
    assert mom["summary"]["is_signal"] is False


def test_reference_strategy_contract():
    with _client_with_data() as c:
        s = c.get("/api/reference/strategy").json()
    assert s["items"]
    counts = s["disposition_counts"]
    assert counts["已实现"] > 0 and counts["不实现（合规）"] > 0
    rejected = [i for i in s["items"] if i["disposition"] == "不实现（合规）"]
    assert all(i["reason"] for i in rejected)
    # 交易指令类规则必须全部落在「不实现」
    assert any("分批买入" in i["title"] for i in rejected)
    assert any("分批卖出" in i["title"] for i in rejected)


def test_homepage_is_an_operator_dashboard():
    """首页只挂信号和数字，不再内嵌方法论长文。"""
    with _client_with_data() as c:
        page = c.get("/")
        overview = c.get("/api/overview").json()
    assert page.status_code == 200
    html = page.text
    assert "国家队资金监控" in html
    assert 'id="signal-label"' in html
    assert "当日成交额" in html and "放量倍数" in html and "份额变化" in html
    assert "/api/overview" in html
    for gone in ("参考策略存档", "噪音屏蔽", "静态演示页面", "跟踪方向与核实", "确权与交叉验证"):
        assert gone not in html
    assert overview["signal"]["state_label"]
    assert "不构成投资建议" in overview["disclaimer"]
