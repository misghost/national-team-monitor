"""定时任务（标准库实现，零额外依赖）。

每个交易日盘后刷新数据并重算信号。容器外部署也可直接用 crontab，
见 deploy/crontab.example。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from . import config

log = logging.getLogger("ntm.scheduler")


def refresh_once(as_of: str | None = None) -> dict:
    """执行一次完整刷新：采集/生成 → 入库 → 重算信号 → 预警。"""
    from . import db
    from .adapters import get_adapter
    from .adapters.demo import DEMO_AS_OF
    from .alert import dispatch

    day = as_of or DEMO_AS_OF
    adapter = get_adapter()
    result = adapter.fetch(day)
    db.save_dataset(result.dataset)
    signal = db.get_latest_signal()
    alerts = dispatch(signal) if signal else []
    return {
        "as_of": day,
        "source": result.source,
        "is_demo": result.is_demo,
        "verified": result.verified,
        "warnings": result.warnings,
        "signals": len(db.get_signals(days=10000)),
        "signal_state": signal["state"] if signal else None,
        "alerts": alerts,
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
