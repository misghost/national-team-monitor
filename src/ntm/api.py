"""FastAPI 应用：三层监控看板 API + 静态页面。

所有 L2 数据在响应里都带 note「估算·不可归因」；
所有信号 is_confirmed 恒为 False。契约见 docs/DATA_DICTIONARY.md。
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import alert, backtest, config, db, datasource, layers
from .adapters import get_adapter
from .entities import as_dict as entities_as_dict
from .etf_pool import POOL_CAVEAT, claims_as_dict, pool_as_dict, validate_pool
from .episodes import load_episodes
from .models import Health, Overview, Signal
from .reference import REFERENCE_STRATEGY
from .signals import evaluate
from .scheduler import refresh_once, start_background_scheduler

log = logging.getLogger("ntm.api")
_refresh_lock = threading.Lock()


def _bootstrap() -> None:
    db.init_db()
    if db.is_empty():
        log.info("数据库为空，装载演示数据集")
        result = get_adapter(demo=True).fetch()
        db.save_dataset(result.dataset)
        db.set_meta("dataset_source", result.source)
        db.set_meta("dataset_is_demo", "1")
        for w in result.warnings:
            log.warning("数据警示：%s", w)


def _is_demo() -> bool:
    return db.get_meta("dataset_is_demo", "1") == "1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    _bootstrap()
    scheduler_thread = None
    if os.getenv("NTM_ENABLE_SCHEDULER", "1") not in {"0", "false", "no"}:
        scheduler_thread = start_background_scheduler()
    app.state.scheduler_thread = scheduler_thread
    yield
    if scheduler_thread is not None:
        stop = getattr(scheduler_thread, "stop_event", None)
        if stop:
            stop.set()


app = FastAPI(title=config.APP_NAME, version=config.VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 生产环境请改为具体域名，见 README
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health", response_model=Health)
def health() -> dict:
    return {"status": "ok", "version": config.VERSION, "is_demo": _is_demo()}


@app.get("/api/overview", response_model=Overview)
def overview() -> dict:
    signal = db.get_latest_signal()
    if signal is None:
        signal = {
            "date": "", "state": "NO_SIGNAL", "state_label": "暂无数据",
            "confidence": "low", "confidence_label": "低", "is_confirmed": False,
            "attribution": "无数据", "summary": "尚未生成信号，请先执行 /api/refresh",
            "conditions": {}, "caveat": "",
        }
    as_of = signal.get("date") or db.get_meta("dataset_as_of", "")
    _attach_trigger_distance(signal)
    return {
        "as_of": as_of,
        "is_demo": _is_demo(),
        "signal": signal,
        "layers": layers.layers_as_dict(),
        "disclaimer": config.DISCLAIMER,
    }


@app.get("/api/etf/shares")
def etf_shares(days: int = Query(30, ge=1, le=365)) -> dict:
    return {"is_demo": _is_demo(), "items": db.get_etf_series(days)}


@app.get("/api/etf/pool")
def etf_pool() -> dict:
    """7 只宽基 ETF 结构化标的池 + 未确权参考观点 + 校验状态。"""
    return {
        "is_demo": _is_demo(),
        "items": pool_as_dict(),
        "claims": claims_as_dict(),
        "caveat": POOL_CAVEAT,
        "validation": validate_pool(),
    }


@app.get("/api/etf/momentum")
def etf_momentum(day: str | None = Query(None)) -> dict:
    """量能异动观测（L3 辅助）。明确 is_signal=False，非买卖信号。"""
    return db.get_etf_momentum(day)


def _attach_trigger_distance(signal: dict) -> None:
    """用当天行情给三个条件补上 value/threshold/gap。不改已存的 met 和状态。"""
    day = signal.get("date") or ""
    if not day:
        return
    inp = db.day_signal_input(day)
    if inp is None:
        return
    fresh = evaluate(inp)["conditions"]
    current = signal.setdefault("conditions", {})
    for key, built in fresh.items():
        cond = current.setdefault(key, {})
        cond["metrics"] = built["metrics"]
        cond.setdefault("met", built["met"])
        cond.setdefault("label", built["label"])
        cond.setdefault("detail", built["detail"])


@app.get("/api/reference/episodes")
def reference_episodes() -> dict:
    """历史对照。data/history_reference.json 不存在时返回空列表，页面不展示。"""
    return {"items": load_episodes()}


@app.get("/api/reference/strategy")
def reference_strategy() -> dict:
    """参考策略原文存档与逐条实现取舍（含不实现项的合规理由）。"""
    return REFERENCE_STRATEGY.as_dict()


@app.get("/api/datasource")
def datasource_status() -> dict:
    """真实/演示数据源接入状态、各层字段契约与缺口。

    只返回「凭据是否已配置」的布尔值，绝不返回任何凭据内容。
    """
    return datasource.current_status()


@app.get("/api/signals")
def signals(days: int = Query(90, ge=1, le=1000)) -> dict:
    return {"is_demo": _is_demo(), "items": db.get_signals(days)}


@app.get("/api/announcements")
def announcements(limit: int = Query(10, ge=1, le=200)) -> dict:
    return {"is_demo": _is_demo(), "items": db.get_announcements(limit)}


@app.get("/api/holdings")
def holdings(limit: int = Query(20, ge=1, le=500)) -> dict:
    return {"is_demo": _is_demo(), "items": db.get_holdings(limit)}


@app.get("/api/methodology")
def methodology() -> dict:
    return {
        "layers": layers.layers_as_dict(),
        "glossary": [dict(g) for g in layers.GLOSSARY],
        "traps": list(layers.TRAPS),
        "noise": list(layers.NOISE),
        "entities": entities_as_dict(),
        "reference_counts": REFERENCE_STRATEGY.disposition_counts,
        "disclaimer": config.DISCLAIMER,
    }


@app.get("/api/backtest")
def backtest_api(horizon: int = Query(20, ge=1, le=120)) -> dict:
    return backtest.run_backtest(horizon).as_dict()


@app.get("/api/alerts")
def alerts_api() -> dict:
    return alert.alert_status()


@app.post("/api/refresh")
def refresh(as_of: str | None = Query(None)) -> JSONResponse:
    if not _refresh_lock.acquire(blocking=False):
        return JSONResponse(status_code=409, content={"detail": "已有刷新任务在执行"})
    try:
        info = refresh_once(as_of)
        info["is_demo"] = _is_demo()
        return JSONResponse(content=info)
    except Exception as exc:  # noqa: BLE001
        log.exception("刷新失败")
        return JSONResponse(status_code=500, content={"detail": type(exc).__name__, "message": str(exc)})
    finally:
        _refresh_lock.release()


# 静态页面必须最后挂载，避免覆盖 /api/* 路由
if config.STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(config.STATIC_DIR), html=True), name="static")


__all__ = ["app"]
