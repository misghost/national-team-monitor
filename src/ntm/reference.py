"""参考策略存档：用户提供的监控方案与交易规则的原文保留与取舍说明。

对应 docs/REFERENCE_STRATEGY.md。这里只做**结构化存档**，不执行任何交易逻辑。

取舍原则（与前几轮研判一致）：
- 客观可观测的数据与流程 → 实现为代码能力；
- 方向性观点、未经确权的判断 → 标记为参考，不写成事实；
- 买卖点位、分批仓位、择时指令 → 原文保留供人工参考，系统不执行、
  不生成、不暗示。理由：公开数据无法日频归因到国家队，且本系统
  定位为「分层监控 + 概率推断」，不是跟单或荐股系统。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

# 处置状态
ADOPTED = "已实现"          # 已落地为代码/接口/界面
KEPT = "保留为参考"          # 原文存档，人工自行判断，系统不执行
REJECTED = "不实现（合规）"  # 与系统定位冲突，明确不实现


@dataclass(frozen=True)
class StrategyItem:
    section: str
    title: str
    original: str           # 用户提供的原始表述（保留）
    disposition: str        # ADOPTED / KEPT / REJECTED
    reason: str
    implementation: str = ""


STRATEGY_ITEMS: tuple[StrategyItem, ...] = (
    # ---- 一、标的池 ----
    StrategyItem(
        "一、标的池", "7 只宽基 ETF 自选池",
        "510050 / 510300 / 510500 / 512100 / 159845 / 588000 / 159915，明确每只跟踪方向",
        ADOPTED, "客观、可核实，是监控的必要基础",
        "etf_pool.py 结构化登记；/api/etf/pool；看板标的池表格",
    ),
    StrategyItem(
        "一、标的池", "159845 名称",
        "材料标注为「易方达中证1000ETF」",
        ADOPTED, "经上交所/新浪财经/中国证券报多源核实，159845 实为华夏中证1000ETF；易方达为 159633",
        "etf_pool.py 已修正为「中证1000ETF华夏」并留核实说明",
    ),
    StrategyItem(
        "一、标的池", "持仓占比 20%–40% / 2026 转向科创50与创业板",
        "「7 只均为国家队持仓占比最高品种，占比普遍 20–40%；2026 后重心转向科创50/创业板」",
        KEPT, "属方向性判断，仅 L1 季度前十大持有人可确权、滞后 1–2 季，无日频公开口径可核实",
        "存为 REFERENCE_CLAIMS 且 claim_verified=False；仅用 is_2026_focus 做前端高亮，不参与信号计算",
    ),

    # ---- 二、监控方案 ----
    StrategyItem(
        "二、监控方案", "方案1：财经工具看板（成交额、环比、量比、放量倍数）",
        "用现有工具查看 7 只 ETF 实时成交额、环比变化、量比，关注放量 2 倍",
        ADOPTED, "成交额/环比/放量倍数客观可算，可作为 L3 辅助观测",
        "momentum.py 纯函数 + /api/etf/momentum + 看板量能异动区",
    ),
    StrategyItem(
        "二、监控方案", "方案1：看板自动统计每日净流入/流出",
        "系统自动统计每日宽基 ETF 净流入/流出规模",
        ADOPTED, "已有 L2 份额×净值估算口径，明确标注为全市场净申购、不可归因",
        "db.get_etf_series + /api/etf/shares（保留既有 note 标注）",
    ),
    StrategyItem(
        "二、监控方案", "方案1：万得/同花顺「ETF资金流向解读」技能与推送",
        "若有万得账号可用技能自动分析申赎净流入；同花顺设置自选放量异动提醒",
        KEPT, "依赖第三方账号与商业数据授权，本系统不内置；且需自行完成合规核验",
        "docs/METHODOLOGY.md 真实数据源接入清单",
    ),
    StrategyItem(
        "二、监控方案", "方案2：自己搭表（东财/上交所下载 + Excel 条件格式标红）",
        "每日下载成交额、前一日成交额、份额变化；环比≥100% 单元格标红；上传 AI 生成异动报告",
        ADOPTED, "等价能力已在系统内实现，且用统一口径避免手工表格口径漂移",
        "量能表格自动计算环比/放量倍数并高亮；免去手工 VLOOKUP",
    ),
    StrategyItem(
        "二、监控方案", "方案3：通达信/文华公式 放量倍数≥2 弹预警",
        "放量倍数 = 当日成交额 / 前5日平均成交额，≥2 时弹出预警",
        ADOPTED, "公式客观、可复现，已实现为纯函数",
        "momentum.volume_multiple / is_spike；阈值可由 NTM_VOLUME_SPIKE_MULTIPLE 覆盖",
    ),
    StrategyItem(
        "二、监控方案", "方案3 联动：放量2倍 + 汇金系前十大持有人占比提升 → 触发买入信号",
        "当某只 ETF 放量 2 倍且前十大持有人中汇金系占比提升时触发买入信号",
        REJECTED,
        "两个条件时间尺度不匹配（日频 vs 季度滞后），且系统不产生买入信号；"
        "「占比提升」需 L1 季度披露，无法与日频放量联动",
        "不实现；L1 持仓在 /api/holdings 独立展示并标注口径陷阱",
    ),

    # ---- 三、交易执行规则 ----
    StrategyItem(
        "三、交易规则", "买入信号：≥3 只放量2倍且含科创50或创业板",
        "视为国家队加仓信号",
        REJECTED,
        "放量属二级市场成交活跃度，不改变 ETF 总份额，无法识别买方身份；"
        "把放量等同于「国家队加仓」是本项目列为噪音屏蔽的典型误读",
        "系统只输出「量能异动共振只数」中性观测，is_signal 恒为 False",
    ),
    StrategyItem(
        "三、交易规则", "分批买入 30% / 30% / 40%",
        "三次信号触发分别买入总仓位 30%、30%、40%",
        REJECTED, "属仓位指令，系统不提供任何买卖建议，不定义买入信号",
        "原文保留于 docs/REFERENCE_STRATEGY.md",
    ),
    StrategyItem(
        "三、交易规则", "标的选择：优先 588000 / 159915，其次 510300",
        "优先买入科创50ETF 与创业板ETF，其次沪深300ETF",
        REJECTED, "属个股/标的选择建议，系统不推荐任何标的",
        "588000/159915 仅以 is_2026_focus 标记「重点观察」，非推荐",
    ),
    StrategyItem(
        "三、交易规则", "卖出信号：≥3 只放量2倍下跌且份额连续3天减少",
        "视为国家队减仓信号",
        REJECTED, "同买入信号：放量下跌无法归因；且系统不定义卖出信号",
        "份额数据可在 /api/etf/shares 查看（L2 估算、不可归因）",
    ),
    StrategyItem(
        "三、交易规则", "分批卖出 30% / 30% / 40%；比国家队晚 1–2 交易日卖出",
        "分批卖出；国家队买卖需十几个工作日，可晚 1–2 日卖避免卖飞",
        REJECTED, "属择时与仓位指令；且国家队实际买卖时点无公开日频数据，无法据此对齐",
        "原文保留于 docs/REFERENCE_STRATEGY.md",
    ),

    # ---- 四、复盘流程 ----
    StrategyItem(
        "四、复盘流程", "每日 10 分钟复盘三步",
        "①查看成交额/环比/放量倍数 ②查上交所份额公告 ③整理表格让 AI 生成分析报告",
        ADOPTED, "流程本身合规且实用，已固化为看板与接口，省去手工整理",
        "看板量能区 + 公告区 + 份额区 + 参考策略页；README 每日复盘章节",
    ),
    StrategyItem(
        "四、复盘流程", "确认国家队持仓是否变化",
        "查看上交所官网 ETF 份额公告，确认国家队持仓变化",
        KEPT, "份额公告为全市场口径，无法识别持有人；国家队持仓只能由 L1 季度披露确权",
        "标注为「份额变化（不可归因）」，不做国家队持仓判断",
    ),

    # ---- 五、风险提示 ----
    StrategyItem(
        "五、风险提示", "国家队动向只作参考、宽基 ETF 也有波动、放量2倍可能误判",
        "资金动向不能完全作为买卖依据；需结合自身风险承受能力；放量信号需结合连续3天份额变化与市场环境",
        ADOPTED, "与本项目方法论一致，已强化",
        "DISCLAIMER / MOMENTUM_CAVEAT / docs/METHODOLOGY.md 风险章节 / 页脚免责",
    ),
)


@dataclass(frozen=True)
class ReferenceStrategy:
    version: str
    title: str
    statement: str
    items: tuple[StrategyItem, ...]
    disposition_counts: dict[str, int]

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "title": self.title,
            "statement": self.statement,
            "items": [asdict(i) for i in self.items],
            "disposition_counts": self.disposition_counts,
        }


def _build() -> ReferenceStrategy:
    counts = {ADOPTED: 0, KEPT: 0, REJECTED: 0}
    for i in STRATEGY_ITEMS:
        counts[i.disposition] = counts.get(i.disposition, 0) + 1
    return ReferenceStrategy(
        version="1.0",
        title="参考策略存档与实现取舍",
        statement=(
            "本节完整保留用户提供的监控方案、交易执行规则、复盘流程与风险提示原文，"
            "并逐条标注处置结论。系统定位为「分层监控 + 带置信度的概率推断」，"
            "不提供任何买卖、仓位或择时指令；所有「国家队资金」判断均为无法确权的概率推断。"
        ),
        items=STRATEGY_ITEMS,
        disposition_counts=counts,
    )


REFERENCE_STRATEGY = _build()

__all__ = ["REFERENCE_STRATEGY", "STRATEGY_ITEMS", "StrategyItem",
           "ADOPTED", "KEPT", "REJECTED"]
