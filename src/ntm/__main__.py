"""命令行入口：python -m ntm <command>"""
from __future__ import annotations

import argparse
import json
import sys

from . import config, db, layers
from .adapters import get_adapter


def cmd_init(_args) -> int:
    db.init_db()
    print(f"数据库已初始化：{db.db_path()}")
    return 0


def cmd_refresh(args) -> int:
    from .scheduler import refresh_once

    info = refresh_once(args.as_of)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


def cmd_serve(_args) -> int:
    import uvicorn

    uvicorn.run("ntm.api:app", host=config.HOST, port=config.PORT, log_level=config.LOG_LEVEL)
    return 0


def cmd_backtest(args) -> int:
    from .backtest import run_backtest

    print(json.dumps(run_backtest(args.horizon).as_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_check(_args) -> int:
    """自检：数据、PIT、实体去重、信号铁律。"""
    from .cleaning import assert_no_lookahead
    from .entities import normalize, unique_core_ids
    from .signals import evaluate, SignalInput, POLICY_BOTTOM_PROBABLE

    db.init_db()
    if db.is_empty():
        result = get_adapter(demo=True).fetch()
        db.save_dataset(result.dataset)

    problems: list[str] = []

    # 1. 证金公司不得重复计数
    a, _, _ = normalize("中国证券金融股份有限公司")
    b, _, _ = normalize("证金公司")
    c, _, _ = normalize("中证金公司")
    if not (a == b == c == "csfc"):
        problems.append("证金系列别名未收敛到同一实体")
    if "csfc" not in unique_core_ids():
        problems.append("核心主体缺少证金")

    # 2. 中投必须被排除
    eid, group, _ = normalize("中国投资有限责任公司")
    if eid is not None or group != "excluded":
        problems.append("中投未被正确排除")

    # 3. PIT：不得存在晚于最新交易日的持仓记录
    latest = db.get_signals(days=1)
    as_of = latest[-1]["date"] if latest else "9999-12-31"
    try:
        assert_no_lookahead(db.get_holdings(limit=10000), as_of, key="effective_date")
    except ValueError as exc:
        problems.append(f"前视偏差：{exc}")

    # 4. 信号铁律：is_confirmed 恒为 False
    sig = evaluate(SignalInput("2026-11-09", -3.0, -6.0, 6, 520.0, 2))
    if sig["is_confirmed"] is not False:
        problems.append("is_confirmed 必须为 False")
    if sig["state"] != POLICY_BOTTOM_PROBABLE or sig["confidence"] == "high":
        problems.append("三重共振状态或置信度上限异常")

    # 5. 三层定义完整
    if len(layers.LAYERS) != 3 or any(not m.attributable for m in layers.LAYERS if m.layer == "L1"):
        problems.append("三层模型定义异常")

    # 6. 全部历史信号均为未确权
    if any(s["is_confirmed"] for s in db.get_signals(days=10000)):
        problems.append("存在被标记为已确权的信号")

    # 7. 标的池完整性、代码唯一、错误修正
    from .etf_pool import ETF_POOL, instrument_by_code, validate_pool
    from .momentum import MOMENTUM_CAVEAT, summarize, build_row
    if len(ETF_POOL) != 7:
        problems.append("标的池应为 7 只")
    if validate_pool():
        problems.append(f"标的池校验未通过：{validate_pool()}")
    e = instrument_by_code("159845")
    if e is None or "华夏" not in e.name or "易方达" in e.name:
        problems.append("159845 未修正为华夏中证1000ETF")

    # 8. 量能观测恒非信号、且不出现买卖指令措辞
    probe = summarize("2026-11-09", [
        build_row(code="510300", name="probe", current=100, previous=40,
                  history=[40] * 5, share_change_yi=0.0),
    ])
    if probe.is_signal is not False:
        problems.append("量能观测被误标为信号")
    for word in ("买入", "卖出", "建仓", "目标价"):
        if word in MOMENTUM_CAVEAT + probe.observation:
            problems.append(f"量能输出含交易指令措辞：{word}")

    if problems:
        for p in problems:
            print(f"[FAIL] {p}", file=sys.stderr)
        return 1
    print("[OK] 自检通过：实体去重 / 中投排除 / PIT 无前视 / 信号未确权 / "
          "三层模型 / 历史信号一致 / 标的池7只且159845已修正 / 量能观测中性")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="ntm", description=config.APP_NAME)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="初始化数据库").set_defaults(func=cmd_init)
    p_refresh = sub.add_parser("refresh", help="刷新数据并重算信号")
    p_refresh.add_argument("--as-of", default=None, help="基准日 YYYY-MM-DD")
    p_refresh.set_defaults(func=cmd_refresh)
    sub.add_parser("serve", help="启动 Web 服务").set_defaults(func=cmd_serve)
    p_bt = sub.add_parser("backtest", help="运行回测")
    p_bt.add_argument("--horizon", type=int, default=20)
    p_bt.set_defaults(func=cmd_backtest)
    sub.add_parser("check", help="自检").set_defaults(func=cmd_check)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
