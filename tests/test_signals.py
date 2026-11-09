"""三重共振信号回归：状态机、置信度上限、未确权铁律。"""
from ntm.signals import (
    ANNOUNCEMENT_ONLY, INFLOW_ONLY, NO_SIGNAL, POLICY_BOTTOM_PROBABLE,
    SignalInput, Thresholds, evaluate,
)

TH = Thresholds()


def _inp(**kw) -> SignalInput:
    base = dict(
        date="2026-11-09", index_change_pct=-3.0, index_change_5d_pct=-6.0,
        sync_etf_count=6, net_subscription_yi=520.0, supportive_announcements=2,
    )
    base.update(kw)
    return SignalInput(**base)


def test_all_three_conditions_is_policy_bottom():
    r = evaluate(_inp(), TH)
    assert r["state"] == POLICY_BOTTOM_PROBABLE
    assert all(c["met"] for c in r["conditions"].values())
    assert r["confidence"] == "medium"        # 基于 L2 的推断不得给 high


def test_never_confirmed():
    """公开数据无法确权，任何状态下 is_confirmed 都必须为 False。"""
    for inp in (
        _inp(),
        _inp(supportive_announcements=0),
        _inp(sync_etf_count=0, net_subscription_yi=0),
        _inp(index_change_pct=2.0, index_change_5d_pct=3.0,
             sync_etf_count=0, net_subscription_yi=0, supportive_announcements=0),
    ):
        r = evaluate(inp, TH)
        assert r["is_confirmed"] is False
        assert "无法归因" in r["attribution"]
        assert r["confidence"] != "high"


def test_etf_only_is_inflow_only_not_national_team():
    r = evaluate(_inp(supportive_announcements=0), TH)
    assert r["state"] == INFLOW_ONLY
    assert "不标记为国家队进场" in r["summary"]


def test_announcement_only_without_fund_flow():
    r = evaluate(_inp(sync_etf_count=0, net_subscription_yi=0.0), TH)
    assert r["state"] == ANNOUNCEMENT_ONLY


def test_no_signal_on_quiet_day():
    r = evaluate(_inp(
        index_change_pct=0.4, index_change_5d_pct=1.1,
        sync_etf_count=1, net_subscription_yi=12.0, supportive_announcements=0,
    ), TH)
    assert r["state"] == NO_SIGNAL


def test_single_etf_spike_does_not_count():
    """单只 ETF 放量不构成同步跳升。"""
    r = evaluate(_inp(sync_etf_count=1, net_subscription_yi=600.0), TH)
    assert r["conditions"]["etf_sync"]["met"] is False
    assert r["state"] != POLICY_BOTTOM_PROBABLE


def test_5d_drop_alone_satisfies_market_condition():
    r = evaluate(_inp(index_change_pct=-0.2, index_change_5d_pct=-5.5), TH)
    assert r["conditions"]["market_drop"]["met"] is True


def test_caveat_mentions_policy_vs_market_bottom():
    assert "政策底不等于市场底" in evaluate(_inp(), TH)["caveat"]
