"""可证伪检验的统计工具（纯标准库，固定种子可复现）。

本模块不产生任何「国家队资金」判断，也不把行情字段顶替份额字段。它只提供
描述性统计、相关（仅作描述性附录）、Bootstrap 置信区间、置换检验与多重比较校正。

设计意图（见 docs/METHODOLOGY.md 第七节）：
- 托底资金是逆周期的，同期相关系数在构造上被扭曲，因此相关只作附录；
- 核心判据是「事件组前瞻收益是否显著区别于非事件日基准」，用置换检验；
- 多个 horizon / 多个阈值会产生多重比较问题，必须做 Bonferroni 校正；
- 进场事件样本为个位数，任何结果都必须附带样本量警示。
"""
from __future__ import annotations

import math
import random
from typing import Callable, Sequence

DEFAULT_SEED = 20261109


def _finite(values: Sequence[float]) -> list[float]:
    out: list[float] = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def mean(values: Sequence[float]) -> float | None:
    xs = _finite(values)
    return sum(xs) / len(xs) if xs else None


def quantile(values: Sequence[float], q: float) -> float | None:
    """线性插值分位数。q∈[0,1]。"""
    xs = sorted(_finite(values))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    q = min(1.0, max(0.0, float(q)))
    pos = (len(xs) - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def summarize(values: Sequence[float]) -> dict:
    """收益分布描述，含左尾（决策最关心的部分）。"""
    xs = _finite(values)
    if not xs:
        return {
            "n": 0, "mean_pct": None, "median_pct": None, "std_pct": None,
            "win_rate_pct": None, "p05_pct": None, "p25_pct": None,
            "min_pct": None, "max_pct": None,
        }
    n = len(xs)
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    return {
        "n": n,
        "mean_pct": round(m, 4),
        "median_pct": quantile(xs, 0.5),
        "std_pct": round(math.sqrt(var), 4),
        "win_rate_pct": round(sum(1 for x in xs if x > 0) / n * 100, 2),
        "p05_pct": quantile(xs, 0.05),
        "p25_pct": quantile(xs, 0.25),
        "min_pct": min(xs),
        "max_pct": max(xs),
    }


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson 相关。样本不足、长度不等、方差为 0 时返回 None（不伪造数值）。"""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    a = _finite(xs)
    b = _finite(ys)
    if len(a) != len(xs) or len(b) != len(ys) or len(a) < 3:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a)
    db = sum((y - mb) ** 2 for y in b)
    if da <= 0 or db <= 0:
        return None
    return num / math.sqrt(da * db)


def _ranks(values: Sequence[float]) -> list[float]:
    """平均秩，处理并列值。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    return pearson(_ranks(list(xs)), _ranks(list(ys)))


def lagged_correlation(
    xs: Sequence[float], ys: Sequence[float], max_lag: int = 60
) -> list[dict]:
    """描述性附录：扫描滞后阶，暴露「窗口自由度」这一陷阱。

    lag>0 表示 y 领先 x（用 y[t-lag] 对齐 x[t]）。返回各 lag 的 Pearson 系数，
    调用方应据此说明「挑窗口可以挑出任意符号」，而不是只报最大值。
    """
    out: list[dict] = []
    n = len(xs)
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a, b = list(xs[lag:]), list(ys[: n - lag])
        else:
            a, b = list(xs[: n + lag]), list(ys[-lag:])
        if len(a) < 3:
            continue
        out.append({"lag": lag, "pearson": pearson(a, b), "n": len(a)})
    return out


def bootstrap_ci(
    values: Sequence[float],
    stat: Callable[[Sequence[float]], float | None] | None = None,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = DEFAULT_SEED,
) -> dict:
    """均值的百分位 Bootstrap 置信区间。固定 seed 保证可复现。"""
    xs = _finite(values)
    stat = stat or mean
    if not xs or n_boot < 1:
        return {"point": None, "ci_low": None, "ci_high": None, "n_boot": n_boot}
    rng = random.Random(seed)
    n = len(xs)
    draws = []
    for _ in range(n_boot):
        sample = [xs[rng.randrange(n)] for _ in range(n)]
        s = stat(sample)
        if s is not None and math.isfinite(s):
            draws.append(s)
    return {
        "point": stat(xs),
        "ci_low": quantile(draws, alpha / 2) if draws else None,
        "ci_high": quantile(draws, 1 - alpha / 2) if draws else None,
        "n_boot": len(draws),
        "alpha": alpha,
    }


def permutation_test(
    group_a: Sequence[float],
    group_b: Sequence[float],
    n_perm: int = 5000,
    seed: int = DEFAULT_SEED,
    two_sided: bool = True,
) -> dict:
    """置换检验：事件组均值 − 对照组均值是否显著区别于随机分组。

    p 值用 (count+1)/(n_perm+1) 校正，避免报告 p=0。
    样本量极小时仍会返回结果，但调用方必须附带样本量警示。
    """
    a, b = _finite(group_a), _finite(group_b)
    if not a or not b or n_perm < 1:
        return {
            "observed_diff_pct": None, "p_value": None, "n_event": len(a),
            "n_control": len(b), "n_perm": n_perm, "significant": False,
        }
    observed = sum(a) / len(a) - sum(b) / len(b)
    pool = a + b
    k = len(a)
    rng = random.Random(seed)
    null: list[float] = []
    for _ in range(n_perm):
        rng.shuffle(pool)
        diff = sum(pool[:k]) / k - sum(pool[k:]) / (len(pool) - k)
        null.append(diff)
    if two_sided:
        extreme = sum(1 for d in null if abs(d) >= abs(observed))
    else:
        extreme = sum(1 for d in null if d >= observed)
    p = (extreme + 1) / (n_perm + 1)
    null_mean = sum(null) / len(null)
    null_std = math.sqrt(sum((d - null_mean) ** 2 for d in null) / len(null))
    return {
        "observed_diff_pct": round(observed, 4),
        "p_value": round(p, 6),
        "null_mean_pct": round(null_mean, 4),
        "null_std_pct": round(null_std, 4),
        "n_event": len(a),
        "n_control": len(b),
        "n_perm": n_perm,
        "two_sided": two_sided,
    }


def bonferroni(p_values: Sequence[float | None], alpha: float = 0.05) -> dict:
    """多 horizon / 多阈值同时检验时的 Bonferroni 校正。

    m 取「实际执行的检验次数」而非有效 p 值个数：某个 horizon 因样本不足
    无法给出 p 值，也不应让校正阈值变宽松（否则等于奖励缺失数据）。
    """
    valid = [p for p in p_values if p is not None]
    m = len(p_values)
    threshold = alpha / m if m else None
    return {
        "tests": m,
        "tests_with_p_value": len(valid),
        "alpha": alpha,
        "adjusted_threshold": threshold,
        "significant": [bool(p is not None and threshold is not None and p < threshold) for p in p_values],
        "any_significant": bool(threshold is not None and any(p < threshold for p in valid)),
    }


def max_drawdown_pct(closes: Sequence[float]) -> float | None:
    xs = _finite(closes)
    if not xs:
        return None
    peak = xs[0]
    worst = 0.0
    for c in xs:
        peak = max(peak, c)
        worst = min(worst, (c / peak - 1) * 100)
    return round(worst, 4)
