"""运行可证伪的事件研究，输出摘要与 JSON 结果。

前置：
  1) python3 scripts/fetch_public_index.py --code 000300.SH --begin 2015-01-01
  2) 人工核对官方原文后填写 data/verified_policy_events.json
     （模板 deploy/verified_policy_events.example.json；不得由程序自动生成）

运行：  PYTHONPATH=src python3 scripts/run_event_study.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ntm import config, event_study as es, events as ev  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="run falsifiable event study")
    ap.add_argument("--index-csv", default="")
    ap.add_argument("--events", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--horizons", default="5,20,60,120")
    ap.add_argument("--split-date", default="")
    ap.add_argument("--n-perm", type=int, default=5000)
    args = ap.parse_args()

    data_dir = config.DATA_DIR
    csv_path = Path(args.index_csv) if args.index_csv else data_dir / "index_000300_sh_daily.csv"
    events_path = Path(args.events) if args.events else data_dir / "verified_policy_events.json"
    out_path = Path(args.out) if args.out else data_dir / "event_study_result.json"

    try:
        series = es.load_index_series(csv_path, label="000300.SH 沪深300")
    except (FileNotFoundError, ValueError) as exc:
        print(f"index data unavailable: {exc}", file=sys.stderr)
        print("run scripts/fetch_public_index.py first", file=sys.stderr)
        return 1

    events = ev.load_events(events_path)
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    cfg = es.EventStudyConfig(
        horizons=horizons,
        n_perm=args.n_perm,
        split_date=args.split_date or None,
    )
    result = es.run_event_study(series, events, cfg)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"index: {result['index']}  {result['index_range'][0]} .. {result['index_range'][1]} ({result['index_days']} days)")
    print(f"source: {result['index_source']}")
    print(f"verified events: {result['events_total']}  status: {result['status']}")
    if result.get("event_dates"):
        print("event dates: " + ", ".join(result["event_dates"]))
    if result["status"] != "COMPLETED":
        print(result["message"])
        return 0

    print("\nhorizon | n | event% | matched% | uncond% | excess% | p(perm) | sig")
    print("-" * 88)
    flags = result["bonferroni"]["significant"]
    for idx, h in enumerate(result["horizons"]):
        e = h["event"]["mean_pct"]
        c = h["control"]["mean_pct"]
        u = h["control_unconditional"]["mean_pct"]
        p = h["permutation"]["p_value"]
        print(
            f"{h['horizon_days']:>7} | {h['event_used']:>2} | "
            f"{e if e is not None else 'NA':>6} | {c if c is not None else 'NA':>8} | "
            f"{u if u is not None else 'NA':>7} | "
            f"{h['excess_mean_pct'] if h['excess_mean_pct'] is not None else 'NA':>7} | "
            f"{p if p is not None else 'NA':>7} | {flags[idx]}"
        )
    print("\n窗口内路径风险（收盘价口径，浮亏为保守下界）")
    print("horizon | event MDD% | event worst-vs-entry% | event breach% | matched MDD%")
    for h in result["horizons"]:
        ep, cp = h["event_path"], h["control_path"]
        print(
            f"{h['horizon_days']:>7} | {ep['mean_max_drawdown_pct']:>10} | "
            f"{ep['mean_worst_vs_entry_pct']:>21} | {ep['breach_rate_pct']:>13} | "
            f"{cp['mean_max_drawdown_pct']:>12}"
        )
    print(f"\nBonferroni: {result['bonferroni']['tests']} tests, "
          f"threshold={result['bonferroni']['adjusted_threshold']}, "
          f"any_significant={result['bonferroni']['any_significant']}")
    print("Baseline: " + result["baseline_note"])
    s = result["sensitivity"]
    print(f"Sensitivity grid: {s['nominally_significant']}/{s['cells']} cells nominally significant (uncorrected)")
    print(f"\njson -> {out_path}")
    print("NOTE: 事件为人工确权的稳市公告，非国家队资金流水；样本个位数，结论仅供参考。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
