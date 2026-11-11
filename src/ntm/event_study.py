"""事件研究：检验「稳市事件后指数前瞻收益是否显著区别于可比基准」。

与 backtest.py 的区别：
- backtest.py 用数据库里的三重共振信号做单 horizon 回测；
- 本模块用人工确权事件清单 + 真实指数日线，做多 horizon、
  **按前期跌幅匹配的条件对照组**、窗口内路径风险、置换检验、
  Bootstrap 置信区间、Bonferroni 校正与稳健性网格。

主对照为什么必须是匹配对照：稳市事件本身是市场对前期超跌的反应，
用「全样本无条件非事件日」做基准会把「起点状态不可比」混入政策效果。
方向并非单一：实证显示大跌后常是**动量延续**而非反弹，无条件基准≈0
既可能低估短期效应、也可能高估长期效应，属**双向陷阱**。
因此主判据用「前 pre_window 日累计涨跌幅最接近」的非事件日匹配组，
无条件对照只作参考，两套基准并列报告；匹配的价值是「用对基准 / 控制动量延续」，
不是「剥离均值回归」。

PIT 铁律：事件在 D 日盘后才可确权，最早入场为 D 的下一交易日收盘
（i0=i+1），绝不使用事件日当日收盘价成交；非交易日映射到其后首个交易日。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import statistics as st

DISCLAIMER = (
    "所有「国家队资金」判断均为基于公开数据的概率推断，无法确权；"
    "本检验使用人工确权的稳市事件，不是国家队资金流水。"
    "不构成投资建议或择时建议。"
)

CAVEATS = [
    "事件清单为人工确权的稳市/增持公告，不是国家队资金流；额度不等于实际买入。",
    "L2 ETF 份额仅代表全市场一级市场净申购，无法识别申购人，本检验未使用 L2 作为主变量。",
    "政策底不等于市场底：主口径＝信号后 60 交易日内最低收盘（2015 年 2850.71），最终底（2638.30）仅用于 120/250 日稳健性；历史间隔约 40 天至 6.6 个月（23~136 交易日），口径须写死。",
    "D = 信息首次公开日（事件锚 / 信号起点），i0 = D+1 收盘（PIT 实际入场），两者不得混用；D 当日入场仅进稳健性网格，属含前视偏差的上界口径。",
    "下行风险分维度定稿：幅度维度（MAE/窗口内最大回撤）无一致方向差异且未经显著性检验；"
    "频率维度跌破入场价率四窗口一致低于匹配对照（58.33/66.67/75.00/83.33 vs 69.58/81.25/87.92/92.50），"
    "Fisher 精确检验双侧 p=0.5228/0.2562/0.1843/0.2445（对照组计数 ±1 敏感性 p∈0.1718~0.5250），"
    "方向一致但统计不显著，属描述性信号、不得写入风控规则；「压缩左尾」仅为待检验假设，不是理论预设或既成事实。",
    "互换便利/回购增持再贷款额度存在「额度→签约→发放→执行→二级买入」五级折损，且与真实托底力度在 2026 年脱钩（自有资金回购替代政策工具，属时变非经典测量误差）；工具用量类变量一律不得作主变量或跨期 pooled 回归的解释变量。",
    "稳市事件为个位数样本且存在事件簇重叠，独立样本数远小于事件条数，统计功效极低。",
    "主对照为按前 20 日累计涨跌幅匹配的非事件日；匹配的价值是「用对基准 / 控制动量延续」，"
    "不是「剥离均值回归」——大跌后实证为动量延续，无条件基准≈0 会双向误估。"
    "匹配仍不能消除政策内生性（事件是对超跌的反应），也未匹配波动率与成交额。",
    "结论对事件集与匹配设定敏感：不同合理设定可给出短期显著或不显著，"
    "必须并列呈现，不得只报对结论有利的那套。",
    "对照组窗口存在重叠与时序自相关，无法完全消除。",
    "终点收益相同不代表路径相同：窗口内峰谷回撤、相对入场价最深浮亏、"
    "跌破入场价比例单列报告，不可用终点收益代替。",
    "多 horizon / 多阈值同时检验会产生多重比较问题，已做 Bonferroni 校正。",
    "不报同期相关系数作为结论：央行明确两项工具内嵌逆周期属性，"
    "同期相关度量的是政策反应函数而非政策效果。",
    "未做撮合级模拟（滑点、涨跌停、停牌），结果不可直接当作可交易收益。",
]


@dataclass
class EventStudyConfig:
    horizons: tuple[int, ...] = (5, 20, 60, 120)
    entry_lag: int = 1
    n_perm: int = 5000
    n_boot: int = 2000
    alpha: float = 0.05
    seed: int = st.DEFAULT_SEED
    exclude_window: int = 20
    control_step: int | None = None  # None → 取 horizon，尽量不重叠
    pre_window: int = 20            # 匹配变量：前 N 日累计涨跌幅
    match_k: int = 20               # 每个事件匹配的非事件日数量
    split_date: str | None = None


@dataclass
class IndexSeries:
    label: str
    source: str
    dates: list[str] = field(default_factory=list)
    closes: list[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.dates)


def load_index_series(path: Path | str, label: str = "", source: str = "") -> IndexSeries:
    """读取 scripts/fetch_public_index.py 产出的 CSV，校验日期升序与收盘价有效性。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"index csv not found: {p}")
    dates: list[str] = []
    closes: list[float] = []
    with p.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            day = (row.get("date") or "").strip()
            try:
                close = float(row["close"])
            except (KeyError, TypeError, ValueError):
                continue
            if len(day) != 10 or close <= 0:
                continue
            if dates and day <= dates[-1]:
                raise ValueError(f"index dates must be strictly ascending: {day} after {dates[-1]}")
            dates.append(day)
            closes.append(close)
    if len(dates) < 3:
        raise ValueError("index series too short")
    return IndexSeries(
        label=label or p.stem,
        source=source or "东方财富公开行情接口 push2his.eastmoney.com（不复权日 K）",
        dates=dates,
        closes=closes,
    )


def _signal_index(dates: list[str], event_date: str) -> int | None:
    """事件日若非交易日，映射到其后首个交易日。

    早于序列起点或晚于序列末端的事件一律返回 None：绝不把区间外事件
    「压」到序列第一天，否则会凭空制造一个伪事件样本。
    """
    if not dates or event_date < dates[0] or event_date > dates[-1]:
        return None
    for i, d in enumerate(dates):
        if d >= event_date:
            return i
    return None


def _forward(closes: list[float], i0: int, horizon: int) -> float | None:
    i1 = i0 + horizon
    if i0 < 0 or i1 >= len(closes) or closes[i0] <= 0:
        return None
    return (closes[i1] / closes[i0] - 1) * 100


def _pre_return(closes: list[float], i: int, pre_window: int) -> float | None:
    """信号日之前 pre_window 个交易日的累计涨跌幅（匹配变量）。"""
    if i - pre_window < 0 or closes[i - pre_window] <= 0:
        return None
    return (closes[i] / closes[i - pre_window] - 1) * 100


def window_path_stats(closes: list[float], i0: int, horizon: int) -> dict | None:
    """窗口内真实路径风险，基于收盘价序列而非收益列表。

    max_drawdown_pct = 窗口内峰谷回撤；worst_vs_entry_pct = 相对入场价最深浮亏；
    breached_entry = 窗口内是否曾跌破入场价；final_pct = 终点收益。
    口径限制：只用收盘价，不含盘中最低价，因此浮亏是保守下界。
    """
    i1 = i0 + horizon
    if i0 < 0 or i1 >= len(closes) or closes[i0] <= 0:
        return None
    seg = closes[i0:i1 + 1]
    entry = seg[0]
    peak = entry
    mdd = 0.0
    for c in seg:
        peak = max(peak, c)
        mdd = min(mdd, (c / peak - 1) * 100)
    low = min(seg)
    return {
        "max_drawdown_pct": round(mdd, 4),
        "worst_vs_entry_pct": round((low / entry - 1) * 100, 4),
        "breached_entry": bool(low < entry),
        "final_pct": round((seg[-1] / entry - 1) * 100, 4),
    }


def _usable_event_indices(
    series: IndexSeries, events: list[dict], horizon: int, entry_lag: int = 1
) -> list[int]:
    out: list[int] = []
    for ev in events:
        i = _signal_index(series.dates, ev["date"])
        if i is None:
            continue
        if _forward(series.closes, i + entry_lag, horizon) is None:
            continue
        out.append(i)
    return out


def event_returns(
    series: IndexSeries, events: list[dict], horizon: int, entry_lag: int = 1
) -> tuple[list[float], list[str]]:
    """每个事件一条前瞻收益；无法计算（超出序列末端）的跳过并显式计数。"""
    values: list[float] = []
    used: list[str] = []
    for ev in events:
        i = _signal_index(series.dates, ev["date"])
        if i is None:
            continue
        r = _forward(series.closes, i + entry_lag, horizon)
        if r is None:
            continue
        values.append(r)
        used.append(ev["date"])
    return values, used


def _blocked_indices(series: IndexSeries, events: list[dict], exclude_window: int) -> set[int]:
    blocked: set[int] = set()
    for ev in events:
        i = _signal_index(series.dates, ev["date"])
        if i is None:
            continue
        for j in range(max(0, i - exclude_window), min(len(series), i + exclude_window + 1)):
            blocked.add(j)
    return blocked


def control_returns(
    series: IndexSeries,
    events: list[dict],
    horizon: int,
    entry_lag: int = 1,
    exclude_window: int = 20,
    step: int | None = None,
) -> list[float]:
    """无条件非事件日基准（参考用，不是主判据）：排除事件窗并按 step 抽样。"""
    step = step or horizon
    blocked = _blocked_indices(series, events, exclude_window)
    values: list[float] = []
    limit = len(series) - entry_lag - horizon - 1
    for i in range(0, limit, step):
        if i in blocked:
            continue
        r = _forward(series.closes, i + entry_lag, horizon)
        if r is not None:
            values.append(r)
    return values


def matched_control_returns(
    series: IndexSeries,
    events: list[dict],
    horizon: int,
    entry_lag: int = 1,
    exclude_window: int = 20,
    pre_window: int = 20,
    match_k: int = 20,
) -> dict:
    """条件匹配对照：为每个事件挑「前期累计涨跌幅最接近」的非事件日。

    目的是让对照组的「超跌程度」与事件组可比，避免把均值回归误算成政策效果。
    无放回抽样；候选不足时按实际可用数量返回，并显式披露 n_matched。
    """
    closes = series.closes
    blocked = _blocked_indices(series, events, exclude_window)
    limit = len(series) - entry_lag - horizon - 1
    candidates: list[dict] = []
    for j in range(pre_window, limit):
        if j in blocked:
            continue
        pre = _pre_return(closes, j, pre_window)
        r = _forward(closes, j + entry_lag, horizon)
        if pre is None or r is None:
            continue
        candidates.append({"idx": j, "pre": pre, "ret": r})

    event_idx = _usable_event_indices(series, events, horizon, entry_lag)
    event_pres = [_pre_return(closes, i, pre_window) for i in event_idx]
    used: set[int] = set()
    matched: list[dict] = []
    for pre_i in event_pres:
        if pre_i is None:
            continue
        ranked = sorted(candidates, key=lambda c: abs(c["pre"] - pre_i))
        taken = 0
        for c in ranked:
            if c["idx"] in used:
                continue
            used.add(c["idx"])
            matched.append(c)
            taken += 1
            if taken >= match_k:
                break

    return {
        "returns": [c["ret"] for c in matched],
        "indices": [c["idx"] for c in matched],
        "pre_event_mean_pct": st.mean([p for p in event_pres if p is not None]),
        "pre_matched_mean_pct": st.mean([c["pre"] for c in matched]),
        "n_matched": len(matched),
        "match_k": match_k,
        "n_candidates": len(candidates),
        "match_variable": f"前 {pre_window} 个交易日累计涨跌幅",
    }


def _path_summary(paths: list[dict]) -> dict:
    empty = {
        "n": 0, "mean_max_drawdown_pct": None, "worst_max_drawdown_pct": None,
        "mean_worst_vs_entry_pct": None, "worst_vs_entry_pct": None,
        "breach_rate_pct": None, "p05_final_pct": None,
    }
    if not paths:
        return empty
    mdd = [p["max_drawdown_pct"] for p in paths]
    wve = [p["worst_vs_entry_pct"] for p in paths]
    fin = [p["final_pct"] for p in paths]
    return {
        "n": len(paths),
        "mean_max_drawdown_pct": round(st.mean(mdd), 4),
        "worst_max_drawdown_pct": round(min(mdd), 4),
        "mean_worst_vs_entry_pct": round(st.mean(wve), 4),
        "worst_vs_entry_pct": round(min(wve), 4),
        "breach_rate_pct": round(100 * sum(1 for p in paths if p["breached_entry"]) / len(paths), 2),
        "p05_final_pct": st.quantile(fin, 0.05),
    }


def _horizon_result(
    series: IndexSeries,
    events: list[dict],
    horizon: int,
    cfg: EventStudyConfig,
) -> dict:
    ev_ret, used = event_returns(series, events, horizon, cfg.entry_lag)
    uncond = control_returns(
        series, events, horizon, cfg.entry_lag, cfg.exclude_window, cfg.control_step
    )
    m = matched_control_returns(
        series, events, horizon, cfg.entry_lag, cfg.exclude_window,
        cfg.pre_window, cfg.match_k,
    )
    primary = m["returns"] or uncond
    baseline = "matched" if m["returns"] else "unconditional"
    perm = st.permutation_test(ev_ret, primary, n_perm=cfg.n_perm, seed=cfg.seed + horizon)
    ctrl_mean = st.mean(primary)
    excess = [r - ctrl_mean for r in ev_ret] if primary else []

    ev_idx = _usable_event_indices(series, events, horizon, cfg.entry_lag)
    ev_paths = [p for p in (window_path_stats(series.closes, i + cfg.entry_lag, horizon) for i in ev_idx) if p]
    ct_paths = [p for p in (window_path_stats(series.closes, j + cfg.entry_lag, horizon) for j in m["indices"]) if p]

    return {
        "horizon_days": horizon,
        "event_used": len(ev_ret),
        "event_dates_used": used,
        "event": st.summarize(ev_ret),
        "control": st.summarize(primary),
        "control_unconditional": st.summarize(uncond),
        "control_matched": st.summarize(m["returns"]),
        "primary_baseline": baseline,
        "matching": {k: v for k, v in m.items() if k not in ("returns", "indices")},
        "excess_mean_pct": round(sum(excess) / len(excess), 4) if excess else None,
        "excess_ci": st.bootstrap_ci(excess, n_boot=cfg.n_boot, alpha=cfg.alpha, seed=cfg.seed + horizon),
        "permutation": perm,
        "event_path": _path_summary(ev_paths),
        "control_path": _path_summary(ct_paths),
        "downside_risk_note": "终点收益相同不代表路径相同；窗口内浮亏须与终点收益并列阅读。",
    }


def sensitivity_grid(
    series: IndexSeries,
    events: list[dict],
    cfg: EventStudyConfig,
    entry_lags: tuple[int, ...] = (1, 2, 3),
) -> dict:
    """稳健性网格：改变入场滞后与各 horizon，观察结论是否翻转。"""
    grid: list[dict] = []
    for lag in entry_lags:
        for h in cfg.horizons:
            ev, _ = event_returns(series, events, h, lag)
            m = matched_control_returns(
                series, events, h, lag, cfg.exclude_window, cfg.pre_window, cfg.match_k
            )
            ct = m["returns"] or control_returns(
                series, events, h, lag, cfg.exclude_window, cfg.control_step
            )
            p = st.permutation_test(ev, ct, n_perm=min(cfg.n_perm, 2000), seed=cfg.seed + lag * 100 + h)
            grid.append({
                "entry_lag": lag,
                "horizon_days": h,
                "baseline": "matched" if m["returns"] else "unconditional",
                "n_event": len(ev),
                "event_mean_pct": round(sum(ev) / len(ev), 4) if ev else None,
                "control_mean_pct": round(sum(ct) / len(ct), 4) if ct else None,
                "p_value": p["p_value"],
            })
    significant = [g for g in grid if g["p_value"] is not None and g["p_value"] < cfg.alpha]
    return {
        "grid": grid,
        "cells": len(grid),
        "nominally_significant": len(significant),
        "note": "仅报告名义 p<alpha 的格子数；未校正的格子显著不代表结论稳健。",
    }


def run_event_study(
    series: IndexSeries,
    events: list[dict[str, Any]],
    cfg: EventStudyConfig | None = None,
) -> dict:
    """主入口。空事件时返回明确结构，绝不合成事件、绝不给出统计结论。"""
    cfg = cfg or EventStudyConfig()
    if not events:
        return {
            "index": series.label,
            "index_source": series.source,
            "index_range": [series.dates[0], series.dates[-1]],
            "index_days": len(series),
            "events_total": 0,
            "status": "NO_VERIFIED_EVENTS",
            "horizons": [],
            "message": "未找到人工确权的稳市事件清单，不执行统计检验，也不合成事件。",
            "is_confirmed": False,
            "caveats": CAVEATS,
            "disclaimer": DISCLAIMER,
        }

    horizons = [_horizon_result(series, events, h, cfg) for h in cfg.horizons]
    pvals = [h["permutation"]["p_value"] for h in horizons]
    b = st.bonferroni(pvals, alpha=cfg.alpha)

    out: dict[str, Any] = {
        "index": series.label,
        "index_source": series.source,
        "index_range": [series.dates[0], series.dates[-1]],
        "index_days": len(series),
        "events_total": len(events),
        "event_dates": [e["date"] for e in events],
        "status": "COMPLETED",
        "config": {
            "horizons": list(cfg.horizons),
            "entry_lag": cfg.entry_lag,
            "n_perm": cfg.n_perm,
            "n_boot": cfg.n_boot,
            "alpha": cfg.alpha,
            "seed": cfg.seed,
            "exclude_window": cfg.exclude_window,
            "control_step": cfg.control_step or "horizon",
            "pre_window": cfg.pre_window,
            "match_k": cfg.match_k,
        },
        "horizons": horizons,
        "bonferroni": b,
        "sensitivity": sensitivity_grid(series, events, cfg),
        "baseline_note": (
            "主判据为按前期跌幅匹配的非事件日对照组；无条件对照组仅作参考，"
            "不得用它计算政策效果。"
        ),
        "is_confirmed": False,
        "attribution": "事件清单为人工确权的稳市公告，无法归因到具体国家队主体或资金规模。",
        "caveats": CAVEATS,
        "note": (
            "检验对象是「稳市事件后指数前瞻收益与下行风险是否区别于可比基准」，"
            "不是「A 股走势与国家队资金的相关性」。样本含事件簇、非独立，"
            "统计功效极低；未校正的显著性不得当作结论。"
        ),
        "disclaimer": DISCLAIMER,
    }

    if cfg.split_date:
        ins = [e for e in events if e["date"] < cfg.split_date]
        outs = [e for e in events if e["date"] >= cfg.split_date]
        out["sample_split"] = {
            "split_date": cfg.split_date,
            "in_sample_events": len(ins),
            "out_of_sample_events": len(outs),
            "out_of_sample_result": _horizon_result(series, outs, 20, cfg) if outs else None,
            "note": "样本外事件数量过少时，该结果仅作记录，不作推断。",
        }
    return out
