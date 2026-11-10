"""演示数据适配器（默认，开箱即跑）。

严格按真实口径生成：固定随机种子、可复现、字段与真实 schema 一致，
并明确标注 is_demo=True。所有页面与 API 都会显示演示数据警示。

生成逻辑刻意构造 3 次「三重共振」事件，用于演示信号与回测链路；
这些数字是**合成的，不是真实行情**。
"""
from __future__ import annotations

import os
import random
from datetime import date, timedelta

from .. import config
from ..adapters.base import BaseAdapter, FetchResult
from ..cleaning import effective_date_of
from ..entities import ENTITIES

SEED = 20261109
DEMO_AS_OF = os.getenv("NTM_AS_OF", "2026-11-09")


def _trading_days(end: date, count: int = 95) -> list[str]:
    days: list[str] = []
    d = end
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= timedelta(days=1)
    return list(reversed(days))


class DemoAdapter(BaseAdapter):
    name = "demo"
    verified = True  # 演示数据本身可复现、可验证；但它不是真实行情

    def fetch(self, as_of: str = DEMO_AS_OF) -> FetchResult:
        rng = random.Random(SEED)
        end = date.fromisoformat(as_of)
        dates = _trading_days(end)
        n = len(dates)
        # 三次合成「托底事件」：最新交易日 + 倒数第 42 / 72 个交易日
        # 最新日对齐事件，便于完整演示三重共振卡片与预警链路
        event_idx = {n - 1, n - 42, n - 72}

        # ---- 指数 ----
        close = 3980.0
        closes: list[float] = []
        changes: list[float] = []
        for i in range(n):
            if i in event_idx:
                chg = rng.uniform(-3.4, -2.4)
            elif i in {k - 1 for k in event_idx}:
                chg = rng.uniform(-2.0, -1.2)
            else:
                chg = rng.gauss(0.05, 0.72)
            chg = round(chg, 2)
            close = round(close * (1 + chg / 100), 2)
            closes.append(close)
            changes.append(chg)

        market: list[dict] = []
        for i, day in enumerate(dates):
            base = closes[max(0, i - 5)]
            chg5 = round((closes[i] / base - 1) * 100, 2) if base else 0.0
            market.append({
                "date": day,
                "index_close": closes[i],
                "index_change_pct": changes[i],
                "index_change_5d_pct": chg5,
                "amount_yi": round(rng.uniform(8500, 15000), 1),
                "margin_balance_yi": round(rng.uniform(27000, 30500), 1),
            })

        # ---- ETF 每日份额（L2 估算）+ 成交额（L3 辅助观测） ----
        # 成交额与份额是两条独立口径：二级市场放量不改变总份额。
        # 刻意构造一个「放量但份额未增加」的反例日，用于界面演示
        # 「放量 2 倍 ≠ 国家队加仓」这一关键区别。
        volume_only_idx = n - 8  # 非事件日，避免干扰三重共振信号
        amount_base = {
            "510300": 62.0, "510050": 34.0, "510500": 41.0,
            "512100": 38.0, "159845": 16.0, "588000": 55.0, "159915": 33.0,
        }
        etf: list[dict] = []
        for i, day in enumerate(dates):
            is_event = i in event_idx
            is_volume_only = i == volume_only_idx
            for code, name in config.CORE_ETF:
                nav = round(rng.uniform(1.2, 4.6), 4)
                if is_event:
                    # 全部核心宽基同步大额净申购，合计超阈值
                    net_yi = round(rng.uniform(62, 115), 2)
                    share_change = net_yi * 1e8 / nav
                elif is_volume_only:
                    # 反例：成交额放量，但份额几乎不变（±3 亿）
                    net_yi = round(rng.uniform(-3, 3), 2)
                    share_change = net_yi * 1e8 / nav
                else:
                    net_yi = round(rng.uniform(-28, 38), 2)
                    share_change = net_yi * 1e8 / nav
                total_share = round(rng.uniform(2.0e10, 9.0e10) + share_change, 2)

                base = amount_base.get(code, 30.0)
                if is_event or is_volume_only:
                    amount = round(base * rng.uniform(2.3, 3.1), 2)
                else:
                    amount = round(base * rng.uniform(0.78, 1.22), 2)
                etf.append({
                    "date": day, "code": code, "name": name,
                    "total_share": total_share, "share_change": round(share_change, 2),
                    "nav": nav, "est_net_yi": net_yi,
                    "amount_yi": amount,
                    "layer": "L2", "source": "demo:etf_total_share+amount",
                })

        # ---- 官方公告（L3） ----
        ann_specs = [
            (dates[n - 1], "中央汇金", "已大额增持宽基 ETF，并将持续买入", "增持公告", True),
            (dates[n - 2], "中国人民银行", "开展第三批证券、基金、保险公司互换便利操作", "政策工具", True),
            (dates[n - 42], "中国国新", "旗下国新投资增持中央企业股票、科技创新类股票及 ETF", "增持公告", True),
            (dates[n - 43], "中国诚通", "诚通金控、诚旸投资增持 ETF 及中央企业股票", "增持公告", True),
            (dates[n - 72], "中央汇金", "扩大 ETF 增持范围，坚定维护资本市场平稳运行", "增持公告", True),
            (dates[n - 12], "国务院国资委", "推动中央企业加强市值管理", "政策表态", False),
            (dates[n - 20], "中国证监会", "例行新闻发布会", "监管动态", False),
            (dates[n - 30], "上海证券交易所", "ETF 份额日常披露", "数据披露", False),
            (dates[n - 55], "财政部", "关于国有资本经营预算的说明", "政策文件", False),
            (dates[n - 85], "中国人民银行", "公开市场操作公告", "流动性", False),
        ]
        announcements = [
            {
                "date": d, "source": s, "title": t, "type": typ,
                "is_supportive": sup, "url": "", "layer": "L3",
            }
            for d, s, t, typ, sup in ann_specs
        ]

        # ---- 季度持仓确权（L1） ----
        report_period = "2026Q3"
        period_end = "2026-09-30"
        disclose = "2026-10-31"
        eff = effective_date_of(period_end, disclose)
        holding_specs = [
            ("601398", "工商银行", "huijin", 1.23e11, 34.71, "不变", False),
            ("601288", "农业银行", "huijin", 1.18e11, 40.03, "不变", False),
            ("601318", "中国平安", "csfc", 5.42e8, 2.98, "不变", False),
            ("000725", "京东方A", "ssf", 1.55e8, 0.42, "名单新进（不可判定为增持）", False),
            ("600050", "中国联通", "guoxin", 1.56e8, 0.51, "名单新进（不可判定为增持）", False),
            ("600863", "华能蒙电", "chengtong", 8.04e7, 1.14, "名单新进（不可判定为增持）", False),
            ("601857", "中国石油", "guoxin", 3.73e8, 0.23, "主动增持", False),
            ("601088", "中国神华", "chengtong", 2.11e8, 1.06, "主动增持", False),
            ("601377", "兴业证券", "ssf", 9.87e7, 1.43, "名单退出（不可判定为减持）", False),
            ("600030", "中信证券", "csfc_asset", 3.62e8, 2.44, "被动变动（复权后无主动增减）", True),
            ("600519", "贵州茅台", "wutongshu", 5.12e6, 0.41, "不变", False),
            ("601668", "中国建筑", "pension", 2.87e8, 0.69, "主动增持", False),
        ]
        by_id = {e.id: e for e in ENTITIES}
        holdings = [
            {
                "code": code, "name": name,
                "entity_id": eid, "entity_name": by_id[eid].name,
                "report_period": report_period, "report_period_end": period_end,
                "disclose_date": disclose, "effective_date": eff,
                "shares": shares, "float_pct": pct,
                "list_type": "十大流通股东", "entry_exit": entry,
                "passive_change": passive,
                "note": "名单进出≠增减持；持股变动需按送转股复权" if "名单" in entry
                        else "PIT：effective_date 取实际披露日",
                "layer": "L1",
            }
            for code, name, eid, shares, pct, entry, passive in holding_specs
        ]

        return FetchResult(
            dataset={
                "market": market, "etf": etf,
                "announcements": announcements, "holdings": holdings,
                "source": f"demo-generated(seed={SEED})", "is_demo": True,
            },
            source=f"demo-generated(seed={SEED}, as_of={as_of})",
            is_demo=True,
            verified=True,
            warnings=[
                "演示数据为合成数据，不代表真实行情，不可用于任何实盘决策",
                "真实数据源需显式开启并自行完成可用性与合规核验",
            ],
        )
