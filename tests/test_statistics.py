"""统计层单元测试：纯函数、可复现、边界不伪造数值。"""
from __future__ import annotations

import math

from ntm import statistics as st


def test_pearson_perfect_positive_and_negative():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert st.pearson(xs, [2 * x + 1 for x in xs]) == 1.0
    assert st.pearson(xs, [-3 * x for x in xs]) == -1.0


def test_pearson_invalid_inputs_return_none():
    assert st.pearson([1.0, 2.0], [1.0, 2.0, 3.0]) is None
    assert st.pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
    assert st.pearson([1.0, 2.0], [3.0, 4.0]) is None
    assert st.pearson([], []) is None


def test_spearman_monotonic():
    xs = [1.0, 2.0, 3.0, 8.0, 20.0]
    ys = [3.0, 5.0, 7.0, 9.0, 11.0]
    assert st.spearman(xs, ys) == 1.0


def test_quantile_interpolation_and_empty():
    assert st.quantile([], 0.5) is None
    assert st.quantile([4.0], 0.05) == 4.0
    assert st.quantile([0.0, 10.0], 0.5) == 5.0


def test_summarize_left_tail_and_win_rate():
    s = st.summarize([-5.0, -1.0, 0.0, 2.0, 4.0])
    assert s["n"] == 5
    assert s["mean_pct"] == 0.0
    assert s["win_rate_pct"] == 40.0
    assert s["p05_pct"] < s["median_pct"]
    assert s["min_pct"] == -5.0


def test_bootstrap_ci_reproducible_and_contains_point():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    a = st.bootstrap_ci(xs, n_boot=400, seed=42)
    b = st.bootstrap_ci(xs, n_boot=400, seed=42)
    assert a == b
    assert a["ci_low"] <= a["point"] <= a["ci_high"]
    assert a["n_boot"] == 400


def test_permutation_test_detects_clear_difference():
    a = [10.0, 11.0, 12.0, 13.0, 14.0]
    b = [-10.0, -9.0, -8.0, -7.0, -6.0]
    r = st.permutation_test(a, b, n_perm=2000, seed=7)
    assert r["observed_diff_pct"] == 20.0
    assert r["p_value"] < 0.01
    assert r["n_event"] == 5 and r["n_control"] == 5


def test_permutation_test_identical_groups_high_p():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    r = st.permutation_test(xs, list(xs), n_perm=1000, seed=7)
    assert r["observed_diff_pct"] == 0.0
    assert r["p_value"] > 0.5


def test_permutation_test_empty_group_returns_none_p():
    r = st.permutation_test([], [1.0, 2.0], n_perm=100)
    assert r["p_value"] is None
    assert r["n_event"] == 0


def test_lagged_correlation_lag_zero_matches_pearson():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    ys = [2.0, 4.0, 5.0, 9.0, 10.0, 13.0]
    rows = st.lagged_correlation(xs, ys, max_lag=2)
    zero = next(r for r in rows if r["lag"] == 0)
    assert math.isclose(zero["pearson"], st.pearson(xs, ys))
    assert any(r["lag"] > 0 for r in rows) and any(r["lag"] < 0 for r in rows)


def test_bonferroni_threshold_and_flags():
    r = st.bonferroni([0.01, 0.04, 0.30], alpha=0.05)
    assert r["tests"] == 3
    assert math.isclose(r["adjusted_threshold"], 0.05 / 3)
    assert r["significant"] == [True, False, False]
    assert r["any_significant"] is True


def test_max_drawdown():
    assert st.max_drawdown_pct([100.0, 120.0, 90.0, 95.0]) == -25.0
    assert st.max_drawdown_pct([]) is None
