"""SQLite 存储层。

只存事实与推断结果，不存任何凭据。所有 L2 记录在写入时即带
layer / source / confidence，避免出现「看起来像实测」的裸数字。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import config
from .signals import SignalInput, evaluate

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_daily (
    date TEXT PRIMARY KEY,
    index_close REAL NOT NULL,
    index_change_pct REAL NOT NULL,
    index_change_5d_pct REAL NOT NULL,
    amount_yi REAL,
    margin_balance_yi REAL
);
CREATE TABLE IF NOT EXISTS etf_daily (
    date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    total_share REAL NOT NULL,
    share_change REAL NOT NULL,
    nav REAL NOT NULL,
    est_net_yi REAL NOT NULL,
    amount_yi REAL NOT NULL DEFAULT 0,   -- 成交额（亿元），L3 辅助观测，非份额
    layer TEXT NOT NULL DEFAULT 'L2',
    source TEXT NOT NULL,
    PRIMARY KEY (date, code)
);
CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    type TEXT NOT NULL,
    is_supportive INTEGER NOT NULL DEFAULT 0,
    url TEXT NOT NULL DEFAULT '',
    layer TEXT NOT NULL DEFAULT 'L3',
    UNIQUE (date, source, title)
);
CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    report_period TEXT NOT NULL,
    report_period_end TEXT NOT NULL,
    disclose_date TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    shares REAL,
    float_pct REAL,
    list_type TEXT NOT NULL DEFAULT '十大流通股东',
    entry_exit TEXT NOT NULL,
    passive_change INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    layer TEXT NOT NULL DEFAULT 'L1',
    UNIQUE (code, entity_id, report_period, list_type)
);
CREATE TABLE IF NOT EXISTS signals (
    date TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    confidence TEXT NOT NULL,
    is_confirmed INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    state TEXT NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_etf_date ON etf_daily(date);
CREATE INDEX IF NOT EXISTS idx_ann_date ON announcements(date);
CREATE INDEX IF NOT EXISTS idx_sig_date ON signals(date);
"""


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量前向迁移：为旧库补齐新增列，避免 CREATE TABLE IF NOT EXISTS 静默忽略。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(etf_daily)")}
    if cols and "amount_yi" not in cols:
        conn.execute("ALTER TABLE etf_daily ADD COLUMN amount_yi REAL NOT NULL DEFAULT 0")


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def is_empty() -> bool:
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM market_daily").fetchone()[0]
    return n == 0


def set_meta(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_meta(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _union_live_history(dataset: dict[str, Any], as_of: str) -> None:
    """实盘刷新只增不删：新数据覆盖同一天，库里已有且不晚于 as_of 的日期必须留下。

    适配器若少返回几天，全量替换会把指数和信号裁短。这里在删除之前把缺口补回。
    上一份数据若仍是演示，不并入，避免合成行情混进实盘。
    """
    if dataset.get("is_demo", True):
        return
    if get_meta("dataset_is_demo", "1") != "0":
        return
    init_db()
    with connect() as conn:
        old_market = conn.execute(
            "SELECT date,index_close,index_change_pct,index_change_5d_pct,"
            "amount_yi,margin_balance_yi FROM market_daily WHERE date<=?",
            (as_of,),
        ).fetchall()
        old_etf = conn.execute(
            "SELECT date,code,name,total_share,share_change,nav,est_net_yi,"
            "amount_yi,layer,source FROM etf_daily WHERE date<=?",
            (as_of,),
        ).fetchall()
    old_max = max((r["date"] for r in old_market), default="")
    market = {r["date"]: r for r in dataset.get("market") or [] if r["date"] <= as_of}
    for r in old_market:
        market.setdefault(r["date"], {
            "date": r["date"],
            "index_close": r["index_close"],
            "index_change_pct": r["index_change_pct"],
            "index_change_5d_pct": r["index_change_5d_pct"],
            "amount_yi": r["amount_yi"],
            "margin_balance_yi": r["margin_balance_yi"],
        })
    dataset["market"] = [market[d] for d in sorted(market)]
    etf = {
        (r["date"], r["code"]): r
        for r in dataset.get("etf") or []
        if r["date"] <= as_of
    }
    for r in old_etf:
        etf.setdefault((r["date"], r["code"]), {
            "date": r["date"], "code": r["code"], "name": r["name"],
            "total_share": r["total_share"], "share_change": r["share_change"],
            "nav": r["nav"], "est_net_yi": r["est_net_yi"],
            "amount_yi": r["amount_yi"], "layer": r["layer"] or "L2",
            "source": r["source"],
        })
    dataset["etf"] = [etf[k] for k in sorted(etf)]
    new_max = max((r["date"] for r in dataset["market"]), default="")
    if old_max and new_max < old_max:
        raise RuntimeError(
            f"刷新后的指数历史截止 {new_max or '空'}，早于库内 {old_max}，已拒绝入库"
        )


def save_dataset(dataset: dict[str, Any], as_of: str | None = None) -> None:
    """原子性替换全量演示/采集数据集，然后重算信号。

    实盘数据会先与库内不晚于 as_of 的历史取并集，避免一次短响应把库裁短。
    """
    init_db()
    if as_of:
        _union_live_history(dataset, as_of)
    with connect() as conn:
        conn.execute("DELETE FROM etf_daily")
        conn.execute("DELETE FROM announcements")
        conn.execute("DELETE FROM holdings")
        conn.execute("DELETE FROM market_daily")
        conn.execute("DELETE FROM signals")

        conn.executemany(
            "INSERT INTO market_daily"
            "(date,index_close,index_change_pct,index_change_5d_pct,amount_yi,margin_balance_yi)"
            " VALUES(?,?,?,?,?,?)",
            [
                (r["date"], r["index_close"], r["index_change_pct"],
                 r["index_change_5d_pct"], r.get("amount_yi"), r.get("margin_balance_yi"))
                for r in dataset["market"]
            ],
        )
        conn.executemany(
            "INSERT INTO etf_daily"
            "(date,code,name,total_share,share_change,nav,est_net_yi,amount_yi,layer,source)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                (r["date"], r["code"], r["name"], r["total_share"], r["share_change"],
                 r["nav"], r["est_net_yi"], r.get("amount_yi", 0.0),
                 r.get("layer", "L2"), r.get("source", "demo"))
                for r in dataset["etf"]
            ],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO announcements"
            "(date,source,title,type,is_supportive,url,layer) VALUES(?,?,?,?,?,?,?)",
            [
                (r["date"], r["source"], r["title"], r["type"],
                 int(bool(r["is_supportive"])), r.get("url", ""), r.get("layer", "L3"))
                for r in dataset["announcements"]
            ],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO holdings"
            "(code,name,entity_id,entity_name,report_period,report_period_end,disclose_date,effective_date,"
            "shares,float_pct,list_type,entry_exit,passive_change,note,layer)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (r["code"], r["name"], r["entity_id"], r["entity_name"],
                 r["report_period"], r["report_period_end"], r["disclose_date"], r["effective_date"],
                 r.get("shares"), r.get("float_pct"), r.get("list_type", "十大流通股东"),
                 r["entry_exit"], int(bool(r.get("passive_change", False))),
                 r.get("note", ""), r.get("layer", "L1"))
                for r in dataset["holdings"]
            ],
        )
    recompute_signals()
    set_meta("dataset_source", str(dataset.get("source", "unknown")))
    set_meta("dataset_is_demo", "1" if dataset.get("is_demo", True) else "0")


def _lookback_dates(day: str, days: int) -> set[str]:
    d = date.fromisoformat(day)
    return {(d - timedelta(days=i)).isoformat() for i in range(days + 1)}


def recompute_signals() -> int:
    """按 PIT 日期重算每日三重共振信号。"""
    with connect() as conn:
        markets = conn.execute("SELECT * FROM market_daily ORDER BY date").fetchall()
        etf = conn.execute("SELECT date, est_net_yi FROM etf_daily").fetchall()
        anns = conn.execute("SELECT date, is_supportive FROM announcements").fetchall()

    etf_by_date: dict[str, list[float]] = {}
    for r in etf:
        etf_by_date.setdefault(r["date"], []).append(r["est_net_yi"])
    supportive_dates = {r["date"] for r in anns if r["is_supportive"]}

    window = config.OFFICIAL_LOOKBACK_DAYS
    results = []
    for m in markets:
        day = m["date"]
        vals = etf_by_date.get(day, [])
        sync_count = sum(1 for v in vals if v > 0)
        net_yi = round(sum(vals), 2)
        supportive = len(supportive_dates & _lookback_dates(day, window))
        res = evaluate(SignalInput(
            date=day,
            index_change_pct=m["index_change_pct"],
            index_change_5d_pct=m["index_change_5d_pct"],
            sync_etf_count=sync_count,
            net_subscription_yi=net_yi,
            supportive_announcements=supportive,
        ))
        results.append((day, res["state"], res["confidence"], 0, json.dumps(res, ensure_ascii=False)))

    with connect() as conn:
        conn.execute("DELETE FROM signals")
        conn.executemany(
            "INSERT INTO signals(date,state,confidence,is_confirmed,payload) VALUES(?,?,?,?,?)",
            results,
        )
    return len(results)


# ---------------- 查询 ----------------

def get_market_row(day: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM market_daily WHERE date=?", (day,)).fetchone()
    return dict(row) if row else None


def get_etf_series(days: int = 30) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT m.date, m.index_close, m.index_change_pct, "
            "COALESCE(SUM(e.est_net_yi),0) AS net_subscription_yi, "
            "COALESCE(SUM(CASE WHEN e.est_net_yi>0 THEN 1 ELSE 0 END),0) AS sync_etf_count "
            "FROM market_daily m LEFT JOIN etf_daily e ON e.date=m.date "
            "GROUP BY m.date ORDER BY m.date DESC LIMIT ?",
            (days,),
        ).fetchall()
    return [
        {
            "date": r["date"],
            "index_close": round(r["index_close"], 2),
            "index_change_pct": round(r["index_change_pct"], 2),
            "net_subscription_yi": round(r["net_subscription_yi"], 2),
            "sync_etf_count": r["sync_etf_count"],
            "note": "L2 估算：份额增量×净值，仅代表全市场一级市场净申购，不可归因",
        }
        for r in reversed(rows)
    ]


def get_etf_momentum(day: str | None = None) -> dict:
    """某交易日 7 只标的的量能观测（L3 辅助，不可归因）。

    成交额与份额是两条独立口径：这里只用 ``amount_yi`` 计算放量倍数，
    并附带同期 ``est_net_yi``（份额变化估算）作对照，供界面展示
    「放量 ≠ 份额增加」的反例。
    """
    from . import momentum
    from .etf_pool import ETF_POOL, POOL_CAVEAT, focus_codes, pool_as_dict

    with connect() as conn:
        if day is None:
            row = conn.execute("SELECT MAX(date) AS d FROM etf_daily").fetchone()
            day = row["d"] if row and row["d"] else ""
        rows = conn.execute(
            "SELECT date,code,name,amount_yi,est_net_yi FROM etf_daily ORDER BY date,code"
        ).fetchall()

    # 按代码聚合有序历史；窗口取「当日之前」的 N 个交易日，避免前视
    by_code: dict[str, list[tuple[str, float, float, str]]] = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(
            (r["date"], r["amount_yi"], r["est_net_yi"], r["name"])
        )
    # 全局交易日序列，用于确定当日之前的窗口
    all_days = sorted({r["date"] for r in rows})
    prior_days = [d for d in all_days if d < day][-config.VOLUME_MA_WINDOW:]

    focus = set(focus_codes())
    threshold = config.VOLUME_SPIKE_MULTIPLE
    out_rows = []
    for e in ETF_POOL:
        series = by_code.get(e.code, [])
        cur = next((x for x in series if x[0] == day), None)
        if cur is None:
            continue
        hist = [x[1] for x in series if x[0] in prior_days]
        prev = next((x[1] for x in reversed(series) if x[0] < day), 0.0)
        out_rows.append(momentum.build_row(
            code=e.code, name=e.name, current=cur[1], previous=prev,
            history=hist, is_2026_focus=e.code in focus,
            share_change_yi=cur[2], threshold=threshold,
        ))

    summary = momentum.summarize(day, out_rows, threshold)
    report = momentum.anomaly_report(out_rows, threshold)
    return {
        "date": day,
        "is_demo": get_meta("dataset_is_demo", "1") == "1",
        "pool": pool_as_dict(),
        "pool_caveat": POOL_CAVEAT,
        "items": [r.as_dict() for r in out_rows],
        "summary": summary.as_dict(),
        "caveat": momentum.MOMENTUM_CAVEAT,
        "anomalies": report["lines"],
        "volume_alert": report["alert"],
    }


def get_signals(days: int = 90) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT date,state,confidence,is_confirmed FROM signals "
            "ORDER BY date DESC LIMIT ?", (days,)
        ).fetchall()
    return [
        {
            "date": r["date"], "state": r["state"],
            "confidence": r["confidence"], "is_confirmed": bool(r["is_confirmed"]),
        }
        for r in reversed(rows)
    ]


def get_latest_signal() -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT payload FROM signals ORDER BY date DESC LIMIT 1"
        ).fetchone()
    return json.loads(row["payload"]) if row else None


def get_announcements(limit: int = 10) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT date,source,title,type,is_supportive,url,layer FROM announcements "
            "ORDER BY date DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [
        {
            "date": r["date"], "source": r["source"], "title": r["title"],
            "type": r["type"], "is_supportive": bool(r["is_supportive"]),
            "url": r["url"], "layer": r["layer"],
        }
        for r in rows
    ]


def get_holdings(limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT code,name,entity_name,report_period,report_period_end,disclose_date,effective_date,"
            "shares,float_pct,list_type,entry_exit,passive_change,note,layer "
            "FROM holdings ORDER BY report_period DESC, shares DESC LIMIT ?", (limit,)
        ).fetchall()
    return [
        {
            "code": r["code"], "name": r["name"], "entity": r["entity_name"],
            "report_period": r["report_period"], "disclose_date": r["disclose_date"],
            "effective_date": r["effective_date"],
            "report_period_end": r["report_period_end"], "shares": r["shares"],
            "float_pct": r["float_pct"], "list_type": r["list_type"],
            "entry_exit": r["entry_exit"], "passive_change": bool(r["passive_change"]),
            "note": r["note"], "layer": r["layer"],
        }
        for r in rows
    ]


def insert_alert(day: str, state: str, channel: str, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO alerts(date,state,channel,status,created_at) VALUES(?,?,?,?,datetime('now'))",
            (day, state, channel, status),
        )


def count_alerts_on(day: str) -> int:
    with connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE date=? AND status='sent'", (day,)
        ).fetchone()[0]


def db_path() -> Path:
    return config.DB_PATH
