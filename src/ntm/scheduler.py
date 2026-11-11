"""定时任务（标准库实现，零额外依赖）。

每个交易日盘后刷新数据并重算信号。容器外部署也可直接用 crontab，
见 deploy/crontab.example。
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta

from . import config

log = logging.getLogger("ntm.scheduler")


def refresh_once(as_of: str | None = None) -> dict:
    """执行一次完整刷新：采集/生成 → 入库 → 重算信号 → 预警。"""
    from . import db
    from .adapters import get_adapter
    from .adapters.demo import DEMO_AS_OF
    from .alert import dispatch

    # 演示模式有固定基准日；真实模式必须用当天，否则定时器每天都会重刷同一天。
    day = as_of or (DEMO_AS_OF if config.DEMO_MODE else date.today().isoformat())
    adapter = get_adapter()
    result = adapter.fetch(day)
    # 真实适配器失败会在此处抛错，不会走到入库，也不会降级到演示数据。
    # as_of 传给入库，实盘历史只增不删。
    db.save_dataset(result.dataset, as_of=day)
    db.set_meta("dataset_as_of", day)
    db.set_meta("dataset_source", result.source)
    db.set_meta("dataset_is_demo", "1" if result.is_demo else "0")
    if result.dataset.get("data_latest_date"):
        db.set_meta("data_latest_date", str(result.dataset["data_latest_date"]))
    # verified 只针对「已接入的真实数据」；演示数据自洽不等于数据源已核验
    db.set_meta("dataset_verified", "1" if (not result.is_demo and result.verified) else "0")
    signal = db.get_latest_signal()
    alerts = dispatch(signal) if signal else []
    from .alert import dispatch_volume

    momentum = db.get_etf_momentum(day)
    volume_alerts: list = []
    if momentum.get("volume_alert"):
        volume_alerts = dispatch_volume(str(momentum.get("date") or day), momentum.get("anomalies") or [])
    return {
        "as_of": day,
        "source": result.source,
        "is_demo": result.is_demo,
        # verified 只描述「已核验的真实数据源」，演示数据自洽不算数据源已核验
        "verified": bool(result.verified and not result.is_demo),
        "warnings": result.warnings,
        "signals": len(db.get_signals(days=10000)),
        "signal_state": signal["state"] if signal else None,
        "alerts": alerts,
        "volume_alerts": volume_alerts,
    }


def _seconds_until_next() -> float:
    now = datetime.now()
    target = now.replace(
        hour=config.REFRESH_HOUR, minute=config.REFRESH_MINUTE, second=0, microsecond=0
    )
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def start_background_scheduler() -> threading.Thread:
    """启动后台线程；返回线程对象便于优雅停止。"""
    stop = threading.Event()

    def loop() -> None:
        log.info("调度器已启动：每日 %02d:%02d 刷新", config.REFRESH_HOUR, config.REFRESH_MINUTE)
        while not stop.is_set():
            wait = _seconds_until_next()
            if stop.wait(timeout=wait):
                break
            try:
                info = refresh_once()
                log.info("刷新完成：%s", info)
            except Exception as exc:  # noqa: BLE001
                log.exception("定时刷新失败：%s", exc)
        log.info("调度器已停止")

    t = threading.Thread(target=loop, name="ntm-scheduler", daemon=True)
    t.stop_event = stop  # type: ignore[attr-defined]
    t.start()
    return t
