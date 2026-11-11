"""事件研究单元测试。

测试数据为**人工构造的等差序列**，仅用于验证代码正确性与 PIT 不变式，
不构成任何市场结论；真实结论必须由 data/ 下的真实行情与人工确权事件产生。
"""
from __future__ import annotations

from ntm import event_study as es
from ntm import events as ev


def _series(n: int = 120, start: float = 100.0, step: float = 0.5) -> es.IndexSeries:
    dates = [f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]
    return es.IndexSeries(
        label="SYNTHETIC",
        source="unit-test synthetic series",
        dates=dates,
        closes=[start + step * i for i in range(n)],
    )


def _events(days: list[str]) -> list[dict]:
    return [
        {"date": d, "name": "test event", "source": "unit test", "verified_by": "test", "note": ""}
        for d in days
    ]


def test_pit_entry_is_next_day_close_not_event_day_close():
    s = _series()
    i = 10
    expected = (s.closes[i + 1 + 5] / s.closes[i + 1] - 1) * 100
    values, used = es.event_returns(s, _events([s.dates[i]]), horizon=5, entry_lag=1)
    assert used == [s.dates[i]]
    assert abs(values[0] - expected) < 1e-9
    wrong = (s.closes[i + 5] / s.closes[i] - 1) * 100
    assert abs(values[0] - wrong) > 1e-9


def test_event_beyond_series_end_is_skipped():
    s = _series(n=30)
    values, used = es.event_returns(s, _events(["2099-01-01"]), horizon=20, entry_lag=1)
    assert values == [] and used == []


def test_event_before_series_start_is_not_mapped_to_first_day():
    s = _series(n=60)
    values, used = es.event_returns(s, _events(["2008-09-18"]), horizon=5, entry_lag=1)
    assert values == [] and used == []


def test_non_trading_event_date_maps_to_next_trading_day():
    s = _series(n=60)
    values, used = es.event_returns(s, _events(["2024-01-01"]), horizon=5, entry_lag=1)
    assert used == ["2024-01-01"]
    assert len(values) == 1


def test_control_excludes_event_window():
    s = _series(n=120)
    evs = _events([s.dates[40]])
    ct = es.control_returns(s, evs, horizon=5, entry_lag=1, exclude_window=10, step=5)
    full = es.control_returns(s, [], horizon=5, entry_lag=1, exclude_window=10, step=5)
    assert len(ct) > 0
    assert len(ct) < len(full)
    assert all(v > 0 for v in ct)


def test_empty_events_never_fabricates_result():
    r = es.run_event_study(_series(), [])
    assert r["status"] == "NO_VERIFIED_EVENTS"
    assert r["horizons"] == []
    assert r["events_total"] == 0
    assert r["is_confirmed"] is False


def test_completed_result_has_bonferroni_and_sensitivity():
    s = _series(n=200)
    evs = _events([s.dates[20], s.dates[60], s.dates[100], s.dates[140]])
    cfg = es.EventStudyConfig(horizons=(5, 20), n_perm=200, n_boot=200, seed=11, exclude_window=5)
    r = es.run_event_study(s, evs, cfg)
    assert r["status"] == "COMPLETED"
    assert len(r["horizons"]) == 2
    assert r["bonferroni"]["tests"] == 2
    assert r["bonferroni"]["tests_with_p_value"] == 2
    assert r["sensitivity"]["cells"] == 6
    assert r["is_confirmed"] is False
    assert len(r["caveats"]) >= 5
    for h in r["horizons"]:
        assert h["event"]["n"] == 4
        assert h["control"]["n"] > 0
        assert 0 < h["permutation"]["p_value"] <= 1


def test_split_date_records_out_of_sample_without_inference():
    s = _series(n=200)
    evs = _events([s.dates[20], s.dates[120]])
    cfg = es.EventStudyConfig(horizons=(5,), n_perm=100, n_boot=100, split_date=s.dates[100])
    r = es.run_event_study(s, evs, cfg)
    assert r["sample_split"]["in_sample_events"] == 1
    assert r["sample_split"]["out_of_sample_events"] == 1


def test_load_events_skips_unverified_and_bad_rows(tmp_path):
    p = tmp_path / "events.json"
    p.write_text(
        """[
        {"date":"2024-01-05","name":"ok","source":"s","verified":true},
        {"date":"2024-01-06","name":"no","source":"s","verified":false},
        {"date":"bad","name":"x","source":"s","verified":true},
        {"date":"2024-01-07","source":"s","verified":true}
        ]""",
        encoding="utf-8",
    )
    out = ev.load_events(p)
    assert len(out) == 1 and out[0]["date"] == "2024-01-05"


def test_window_path_stats_catches_intra_window_drawdown():
    # 终点为正（+15%），但中途从 120 回撤到 90，且跌破入场价 100
    p = es.window_path_stats([100.0, 120.0, 90.0, 115.0], 0, 3)
    assert p["final_pct"] == 15.0
    assert p["max_drawdown_pct"] == -25.0
    assert p["worst_vs_entry_pct"] == -10.0
    assert p["breached_entry"] is True


def test_matched_control_pre_return_is_close_to_event_group():
    s = _series(n=300)
    evs = _events([s.dates[100], s.dates[200]])
    m = es.matched_control_returns(s, evs, 20, pre_window=20, match_k=10)
    assert m["n_matched"] > 0
    assert m["n_candidates"] > 0
    assert abs(m["pre_matched_mean_pct"] - m["pre_event_mean_pct"]) < 1.0


def test_horizon_result_reports_separate_unconditional_and_matched_baselines():
    s = _series(n=300)
    evs = _events([s.dates[100], s.dates[200]])
    cfg = es.EventStudyConfig(horizons=(20,), n_perm=200, n_boot=100, exclude_window=5)
    h = es._horizon_result(s, evs, 20, cfg)
    assert h["primary_baseline"] == "matched"
    assert h["control_unconditional"]["n"] > 0
    assert h["control_matched"]["n"] > 0
    assert h["event_path"]["n"] == 2
    assert h["control_path"]["n"] > 0
    assert h["event_path"]["mean_max_drawdown_pct"] is not None


def test_load_events_missing_file_returns_empty(tmp_path):
    assert ev.load_events(tmp_path / "missing.json") == []
