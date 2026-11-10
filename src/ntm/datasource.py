"""真实数据源接入契约与状态。

本模块**不内置任何未经核验的第三方接口地址**，也不抓取任何外部数据。
它只定义：真实数据必须满足什么字段口径、当前接入到哪一步、还缺什么。

三条铁律（与 docs/METHODOLOGY.md 一致）：
1. 演示数据与真实数据必须可区分，禁止把合成数据标为真实；
2. 真实适配器失败时抛错并停止，绝不降级回演示数据；
3. 接入真实数据不改变归因边界：L2 永远 attributable=False，
   任何信号 is_confirmed 永远 False —— 真实数据不会让「国家队跟单」成立。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from . import config, db

# ---- 各层真实数据的必需字段（口径红线） ----
MARKET_REQUIRED = ("date", "index_close", "index_change_pct", "index_change_5d_pct")
ETF_REQUIRED = (
    "date", "code", "name", "total_share", "share_change",
    "nav", "est_net_yi", "source",
)
ANNOUNCEMENT_REQUIRED = ("date", "source", "title", "type", "is_supportive")
HOLDINGS_REQUIRED = (
    "code", "name", "entity_id", "entity_name", "report_period", "report_period_end",
    "disclose_date", "effective_date", "list_type", "entry_exit",
)

DATASET_KEYS = ("market", "etf", "announcements", "holdings")

# 严禁用来顶替「ETF 总份额」的行情/资金流字段
FORBIDDEN_SUBSTITUTES = {
    "amount_yi": "二级市场成交额",
    "main_inflow": "主力资金流入",
    "net_inflow": "资金净流入",
    "volume": "成交量",
    "turnover": "换手率",
}

DEMO_SOURCE_MARKERS = ("demo", "synthetic", "mock", "fake", "模拟", "演示")

IRON_RULES = (
    "真实数据请求失败即报错停止，绝不降级回演示数据或以合成数据填充",
    "L2 只取 ETF 总份额与净值；成交额、放量倍数、主力资金流入一律不能替代份额",
    "接入真实数据不改变归因边界：L2 attributable=False，所有信号 is_confirmed=False",
    "「谁在申购 ETF」无公开日频数据，真实数据只能让 L2 估算更准，不能确权到国家队",
)

# 基于 2026-11-10 对 Wind 万得公众号推文及官方接入协议的核查结论。
# 结论只陈述可核实的事实；未证实的项目显式标为 None，不做推断。
WIND_ALICE_REVIEW = {
    "article_title": "重磅：Alice Market个人订阅正式上线！A股行情免费用!",
    "publisher": "Wind万得",
    "published_at": "2026-11-10",
    "is_data_source": False,
    "what_it_is": "商业产品推广：Wind MCP / Skill / Agent 个人订阅（体验版 49 元/月起）",
    "official_site": "https://market.windalice.com",
    "install_protocol": "https://aifinmarket.wind.com.cn/skill.md",
    "credential": "WIND_API_KEY（需在 Alice Market 注册后自行生成）",
    "mcp_servers": (
        "stock_data", "fund_data", "index_data", "bond_data",
        "financial_docs", "economic_data", "analytics_data",
    ),
    "free_scope": "官方表述为「A股行情免费」，具体字段清单以官网说明为准，推文未列出",
    "covers_etf_total_share": None,
    "covers_quarterly_holders": None,
    "covers_official_announcements": None,
    "api_endpoint_in_article": None,
    "field_spec_in_article": None,
    "sample_response_in_article": None,
    "verdict": (
        "推文未给出任何接口地址、参数、返回字段或样例，本身不是数据源，"
        "不能据此完成接入；接入需先注册并取得 WIND_API_KEY，且需核对其工具契约。"
    ),
    "gaps_for_this_platform": (
        "L2 核心字段为 ETF 每日总份额（一级市场申赎），不属于「行情」范畴，需单独确认 fund_data 是否提供",
        "L1 需要季度十大流通股东 / ETF 持有人及真实披露日，属于财报/公告类数据，需单独确认权限",
        "L3 需要央行/汇金/证监会/国新·诚通等官方稳市公告的结构化结果",
        "WorkBuddy 会话级临时凭证（12 小时）无法作为云服务器长期后台数据源",
    ),
}


# 2026-11-10 实测核验后的 Wind MCP 接入实况（与上方"推文核查"是两件事）。
WIND_MCP_INTEGRATION = {
    "status": "已接入（凭据由接入方自行提供，仅存于服务器环境变量）",
    "verified_at": "2026-11-10",
    "protocol": "MCP Streamable HTTP (stateless) / JSON-RPC 2.0 / market_call_tool",
    "tools": {
        "get_index_kline": "沪深300日K区间 → 收盘价、1日/5日涨跌幅、成交额（条件①）",
        "fund_get_size": "7 只宽基 ETF 场内流通份额及当日份额变化（L2 核心，日频、可回溯）",
        "fund_get_listed_historical_price": "ETF 成交额与 IOPV（净值口径、量能观测）",
    },
    "l2_field": (
        "f_unit_floortrading（场内流通份额，份）→ total_share；"
        "f_unit_floortradingchange → share_change；f_unit_changedate → 真实数据日期"
    ),
    "l1_gap": "Wind MCP 未提供十大流通股东明细，L1 保持空列表并显式标注缺口",
    "l3_policy": "官方公告不自动确权，仅从 data/verified_announcements.json 读取人工核验记录",
    "refresh": "每交易日盘后定时刷新 + POST /api/refresh 手动即时刷新",
    "note": "ETF 份额为日频数据（T+1 更新），非逐笔实时；数据日期以 Wind 返回的份额变动日期为准。",
}


class LiveDataError(RuntimeError):
    """真实数据源未配置、不可用或未通过校验。

    抛出后系统必须停止本次采集，禁止回退到演示数据。
    """


def _is_iso(value: Any) -> bool:
    try:
        date.fromisoformat(str(value))
        return True
    except (TypeError, ValueError):
        return False


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_dataset(dataset: dict[str, Any], as_of: str) -> list[str]:
    """校验一份「声称是真实数据」的数据集，返回问题列表（空列表=通过）。

    校验失败的记录一律不得入库。这是防止「真实数据」标签被伪造的闸门。
    """
    problems: list[str] = []
    if not isinstance(dataset, dict):
        return ["数据集必须是 dict"]

    for key in DATASET_KEYS:
        if key not in dataset:
            problems.append(f"缺少数据层 {key}")
        elif not isinstance(dataset[key], list):
            problems.append(f"{key} 必须是列表")

    if problems:
        return problems

    if not _is_iso(as_of):
        problems.append(f"as_of 不是合法 ISO 日期：{as_of}")

    valid_codes = {code for code, _ in config.CORE_ETF}

    # --- market（条件① 市场大跌） ---
    if not dataset["market"]:
        problems.append("market 为空：缺少指数行情，无法判定条件①")
    for i, r in enumerate(dataset["market"]):
        if not isinstance(r, dict):
            problems.append(f"market[{i}] 不是对象")
            continue
        missing = [k for k in MARKET_REQUIRED if k not in r]
        if missing:
            problems.append(f"market[{i}] 缺字段 {missing}")
            continue
        if not _is_iso(r["date"]):
            problems.append(f"market[{i}].date 非法")
        elif _is_iso(as_of) and str(r["date"]) > as_of:
            problems.append(f"market[{i}].date 晚于 as_of，存在前视偏差")
        for k in ("index_close", "index_change_pct", "index_change_5d_pct"):
            if not _is_num(r[k]):
                problems.append(f"market[{i}].{k} 非数值")

    # --- etf（条件② 宽基份额同步，L2 核心） ---
    if not dataset["etf"]:
        problems.append("etf 为空：缺少 ETF 总份额，无法判定条件②")
    seen: set[tuple[str, str]] = set()
    for i, r in enumerate(dataset["etf"]):
        if not isinstance(r, dict):
            problems.append(f"etf[{i}] 不是对象")
            continue
        missing = [k for k in ETF_REQUIRED if k not in r]
        if missing:
            bad = [f"{k}（{FORBIDDEN_SUBSTITUTES[k]}）" for k in FORBIDDEN_SUBSTITUTES if k in r]
            if "total_share" in missing and bad:
                problems.append(
                    f"etf[{i}] 缺 total_share 却提供了 {', '.join(bad)}："
                    "行情/资金流字段不能代替 ETF 总份额"
                )
            else:
                problems.append(f"etf[{i}] 缺字段 {missing}")
            continue
        code = str(r["code"])
        if code not in valid_codes:
            problems.append(f"etf[{i}].code={code} 不在 7 只标的池内")
        if not _is_num(r["total_share"]) or r["total_share"] <= 0:
            problems.append(f"etf[{i}].total_share 必须为正数值（总份额，非成交额）")
        if not _is_num(r["nav"]) or r["nav"] <= 0:
            problems.append(f"etf[{i}].nav 必须为正数值")
        if not _is_num(r["share_change"]) or not _is_num(r["est_net_yi"]):
            problems.append(f"etf[{i}] 份额增量/净申购额非数值")
        if not _is_iso(r["date"]):
            problems.append(f"etf[{i}].date 非法")
        elif _is_iso(as_of) and str(r["date"]) > as_of:
            problems.append(f"etf[{i}].date 晚于 as_of，存在前视偏差")
        src = str(r["source"]).lower()
        if not src or any(m in src for m in DEMO_SOURCE_MARKERS):
            problems.append(f"etf[{i}].source 不得为空或含演示/合成标记：{r['source']}")
        if r.get("layer", "L2") != "L2":
            problems.append(f"etf[{i}].layer 必须为 L2")
        if r.get("attributable") is True:
            problems.append(f"etf[{i}] 不得标记为可归因（L2 永远 attributable=False）")
        key = (str(r["date"]), code)
        if key in seen:
            problems.append(f"etf[{i}] 重复记录 (date={key[0]}, code={code})")
        seen.add(key)

    # --- announcements（条件③ 官方公告） ---
    for i, r in enumerate(dataset["announcements"]):
        if not isinstance(r, dict):
            problems.append(f"announcements[{i}] 不是对象")
            continue
        missing = [k for k in ANNOUNCEMENT_REQUIRED if k not in r]
        if missing:
            problems.append(f"announcements[{i}] 缺字段 {missing}")
            continue
        if not _is_iso(r["date"]):
            problems.append(f"announcements[{i}].date 非法")
        elif _is_iso(as_of) and str(r["date"]) > as_of:
            problems.append(f"announcements[{i}].date 晚于 as_of，存在前视偏差")
        if not isinstance(r["is_supportive"], bool):
            problems.append(f"announcements[{i}].is_supportive 必须是布尔值")

    # --- holdings（L1 季度确权，允许为空但必须做 PIT 校验） ---
    for i, r in enumerate(dataset["holdings"]):
        if not isinstance(r, dict):
            problems.append(f"holdings[{i}] 不是对象")
            continue
        missing = [k for k in HOLDINGS_REQUIRED if k not in r]
        if missing:
            problems.append(f"holdings[{i}] 缺字段 {missing}")
            continue
        if not _is_iso(r["disclose_date"]) or not _is_iso(r["effective_date"]):
            problems.append(f"holdings[{i}] 披露日/生效日非法")
            continue
        if str(r["effective_date"]) < str(r["disclose_date"]):
            problems.append(f"holdings[{i}] effective_date 早于 disclose_date，存在前视偏差")
        if _is_iso(as_of) and str(r["effective_date"]) > as_of:
            problems.append(f"holdings[{i}] effective_date 晚于 as_of，存在前视偏差")

    return problems


def current_status(meta: dict[str, str] | None = None) -> dict[str, Any]:
    """返回当前数据源接入状态。绝不返回任何凭据值，只返回「是否已配置」。"""
    if meta is None:
        db.init_db()
        meta = {
            "dataset_is_demo": db.get_meta("dataset_is_demo", "1"),
            "dataset_source": db.get_meta("dataset_source", "unknown"),
            "dataset_as_of": db.get_meta("dataset_as_of", ""),
            "dataset_verified": db.get_meta("dataset_verified", "0"),
            "data_latest_date": db.get_meta("data_latest_date", ""),
        }

    is_demo = meta.get("dataset_is_demo", "1") != "0"
    verified = meta.get("dataset_verified", "0") == "1"
    mode = "demo" if is_demo else ("live" if verified else "snapshot")
    credential_ready = bool(config.data_api_key())
    endpoint_ready = bool(config.data_endpoint())
    verified_env = config.data_verified()

    # 各层"已接入"按真实记录数判定：切到真实数据不等于三层都齐了。
    counts = {"L1": 0, "L2": 0, "L3": 0}
    try:
        with db.connect() as conn:
            counts["L1"] = conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
            counts["L2"] = conn.execute("SELECT COUNT(*) FROM etf_daily").fetchone()[0]
            counts["L3"] = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    except Exception:  # noqa: BLE001  状态接口不应因只读查询失败而 500
        pass

    layer_notes = {
        "L1": "季度十大流通股东 / ETF 持有人，需真实披露日与 PIT 防前视",
        "L2": "ETF 每日总份额 × 净值；不可归因，禁止用行情或资金流字段替代",
        "L3": "官方稳市公告 / 政策工具 / 融资余额等代理指标",
    }
    layers = [
        {
            "layer": layer,
            "connected": (not is_demo) and counts[layer] > 0,
            "verified": verified and counts[layer] > 0,
            "records": counts[layer],
            "source": meta.get("dataset_source", "unknown") if not is_demo else "demo-synthetic",
            "required_fields": list(req),
            "note": layer_notes[layer],
            "attributable": layer == "L1",
        }
        for layer, req in (
            ("L1", HOLDINGS_REQUIRED), ("L2", ETF_REQUIRED), ("L3", ANNOUNCEMENT_REQUIRED)
        )
    ]

    gaps: list[str] = []
    if is_demo:
        gaps.append("当前数据集为演示合成数据，真实数据源尚未接入")
    if not endpoint_ready:
        gaps.append("未配置 NTM_DATA_ENDPOINT（需指向已完成核验的真实数据端点）")
    if not credential_ready:
        gaps.append("未配置 NTM_DATA_API_KEY（凭据仅从环境变量读取，不落盘、不打印）")
    if not verified_env:
        gaps.append("NTM_DATA_VERIFIED=0：docs/METHODOLOGY.md 第八节核验清单尚未完成")
    if not is_demo and (counts["L1"] == 0 or counts["L3"] == 0):
        gaps.append(
            "L1 季度十大流通股东 / L3 官方公告未自动接入："
            "Wind MCP 无十大流通股东明细；公告仅接受人工核验录入，系统不自动判定 supportive"
        )
    # 以下为恒定约束，接入真实数据后依然成立
    gaps.append("L2 必须提供 ETF 总份额；价格、成交额、放量倍数、主力资金流入均不能替代")
    gaps.append("L1 必须提供真实披露日（disclose_date），不能用报告期末代替")
    gaps.append("数据源凭据仅从环境变量读取，不落盘、不打印；服务器后台须使用长期有效的 API Key")

    return {
        "mode": mode,
        "mode_label": {
            "demo": "演示数据（合成）",
            "snapshot": "真实数据快照（未核验）",
            "live": "真实数据（已核验）",
        }[mode],
        "is_demo": is_demo,
        "verified": verified,
        "dataset_source": meta.get("dataset_source", "unknown"),
        "dataset_as_of": meta.get("dataset_as_of", ""),
        "data_latest_date": meta.get("data_latest_date", ""),
        "wind_mcp": WIND_MCP_INTEGRATION,
        "credential_configured": credential_ready,
        "endpoint_configured": endpoint_ready,
        "verification_flag": verified_env,
        "ready_to_switch": endpoint_ready and credential_ready and verified_env,
        "layers": layers,
        "gaps": gaps,
        "iron_rules": list(IRON_RULES),
        "wind_alice_review": WIND_ALICE_REVIEW,
        "attribution_note": (
            "即使接入真实数据，谁在申购 ETF 依然不可知；"
            "真实数据只会让 L2 估算更准确，不会让 is_confirmed 变为 True。"
        ),
        "disclaimer": config.DISCLAIMER,
    }


__all__ = [
    "LiveDataError", "validate_dataset", "current_status", "WIND_ALICE_REVIEW",
    "WIND_MCP_INTEGRATION",
    "ETF_REQUIRED", "FORBIDDEN_SUBSTITUTES", "IRON_RULES",
]
