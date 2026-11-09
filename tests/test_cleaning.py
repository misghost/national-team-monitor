"""清洗规则回归：名单进出≠增减持、送转股复权、PIT 防前视。"""
import pytest

from ntm.cleaning import (
    DECREASE, INCREASE, LIST_ENTRY, LIST_EXIT, PASSIVE_ONLY,
    UNCHANGED, UNKNOWN, LIST_TOP10, LIST_TOP10_FLOAT,
    Snapshot, assert_no_lookahead, classify_change, effective_date_of, pit_filter,
)


def test_list_entry_is_not_an_increase():
    r = classify_change(Snapshot(None), Snapshot(1_000_000))
    assert r.classification == LIST_ENTRY
    assert r.is_actionable is False


def test_list_exit_is_not_a_decrease():
    r = classify_change(Snapshot(1_000_000), Snapshot(None))
    assert r.classification == LIST_EXIT
    assert r.is_actionable is False


def test_corporate_action_is_passive_only():
    """10 转 3：持股数 1000 万 → 1300 万，全部由送转解释，不是增持。"""
    r = classify_change(Snapshot(10_000_000), Snapshot(13_000_000), corporate_action_factor=1.3)
    assert r.classification == PASSIVE_ONLY
    assert r.is_actionable is False
    assert r.delta_shares == 0.0


def test_real_increase_after_adjustment():
    r = classify_change(Snapshot(10_000_000), Snapshot(15_000_000), corporate_action_factor=1.3)
    assert r.classification == INCREASE
    assert r.is_actionable is True
    assert r.delta_shares == 2_000_000


def test_real_decrease():
    r = classify_change(Snapshot(10_000_000), Snapshot(8_000_000))
    assert r.classification == DECREASE
    assert r.is_actionable is True


def test_unchanged():
    assert classify_change(Snapshot(500), Snapshot(500)).classification == UNCHANGED


def test_mixed_list_types_rejected():
    r = classify_change(
        Snapshot(100, LIST_TOP10), Snapshot(200, LIST_TOP10_FLOAT)
    )
    assert r.classification == UNKNOWN and r.is_actionable is False


def test_effective_date_is_disclosure_date():
    assert effective_date_of("2026-09-30", "2026-10-31") == "2026-10-31"


def test_effective_date_rejects_impossible_disclosure():
    with pytest.raises(ValueError):
        effective_date_of("2026-09-30", "2026-09-01")


def test_pit_filter_and_no_lookahead():
    rows = [
        {"effective_date": "2026-10-31", "v": 1},
        {"effective_date": "2027-01-31", "v": 2},
    ]
    assert [r["v"] for r in pit_filter(rows, "2026-11-09")] == [1]
    with pytest.raises(ValueError, match="前视偏差"):
        assert_no_lookahead(rows, "2026-11-09")
