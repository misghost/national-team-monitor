"""演示数据可复现性 + 数据库与信号链路回归。"""
from ntm import db
from ntm.adapters.demo import DemoAdapter

EXPECTED_KEYS = {"market", "etf", "announcements", "holdings", "source", "is_demo"}


def test_demo_is_reproducible():
    a = DemoAdapter().fetch().dataset
    b = DemoAdapter().fetch().dataset
    assert a == b, "固定种子下演示数据必须完全一致"


def test_demo_schema_and_layer_labels():
    ds = DemoAdapter().fetch().dataset
    assert EXPECTED_KEYS <= set(ds)
    assert ds["is_demo"] is True
    assert all(r["layer"] == "L2" for r in ds["etf"])
    assert all(r["layer"] == "L1" for r in ds["holdings"])
    assert all(r["layer"] == "L3" for r in ds["announcements"])
    # PIT 字段齐备：报告期末 → 披露日 → 最早可用日 单调不减
    # 注意：report_period 是季度标签（如 2026Q3），不是日期，必须另存 report_period_end
    from datetime import date as Date

    for h in ds["holdings"]:
        assert h["effective_date"] == h["disclose_date"] >= h["report_period_end"]
        assert isinstance(Date.fromisoformat(h["report_period_end"]), Date)
        assert "Q" in h["report_period"] and h["report_period"].startswith("20")


def test_save_recompute_and_query():
    ds = DemoAdapter().fetch().dataset
    db.save_dataset(ds)

    assert not db.is_empty()
    series = db.get_etf_series(days=30)
    assert len(series) == 30
    assert all("L2 估算" in r["note"] for r in series)
    assert all(r["is_confirmed"] is False for r in db.get_signals(days=1000))
    assert db.get_latest_signal()["is_confirmed"] is False
    assert len(db.get_announcements(limit=5)) == 5
    assert len(db.get_holdings(limit=5)) == 5


def test_at_least_one_policy_bottom_event_in_demo():
    db.save_dataset(DemoAdapter().fetch().dataset)
    states = {s["state"] for s in db.get_signals(days=1000)}
    assert "POLICY_BOTTOM_PROBABLE" in states


def test_holdings_effective_dates_are_pit_safe():
    from ntm.cleaning import assert_no_lookahead

    db.save_dataset(DemoAdapter().fetch().dataset)
    latest = db.get_signals(days=1)[-1]["date"]
    assert_no_lookahead(db.get_holdings(limit=10000), latest, key="effective_date")
