"""三层数据模型与置信度规则。

这是整个系统的方法论底座，对应研判报告的 P0-2 / P0-3：
公开数据无法日频归因到国家队，因此必须显式区分「实测 / 估算 / 代理」，
并规定 L2、L3 永远不可归因、L2 永远不得表述为「国家队买入 X 亿」。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

L1 = "L1"
L2 = "L2"
L3 = "L3"


@dataclass(frozen=True)
class LayerMeta:
    layer: str
    name: str
    timeliness: str
    attributable: bool          # 是否可归因到具体国家队主体
    confidence: str             # high / medium / low
    sources: str
    rule: str

    def as_dict(self) -> dict:
        return asdict(self)


LAYERS: tuple[LayerMeta, ...] = (
    LayerMeta(
        L1, "实测（季度确权）", "滞后 1–2 季", True, "high",
        "上市公司十大流通股东 / 十大股东、ETF 半年报与年报前十大持有人",
        "可确权主体与持股数；但名单进出≠增减持，需复权与 PIT 处理",
    ),
    LayerMeta(
        L2, "估算（T+1）", "T+1 盘后", False, "low",
        "宽基 ETF 每日总份额变动 × 当日净值",
        "仅代表全市场一级市场净申购，无法识别申购人；不得表述为「国家队买入」",
    ),
    LayerMeta(
        L3, "代理 / 交叉验证", "T+0 ~ T+1", False, "medium",
        "央行 / 汇金 / 证监会 / 国资委 / 国新·诚通公告、互换便利、回购增持再贷款、融资余额、成交额",
        "公告可确权「政策意图」，但不含具体成交明细；额度≠实际买入金额",
    ),
)

_BY_LAYER = {m.layer: m for m in LAYERS}


def meta(layer: str) -> LayerMeta:
    if layer not in _BY_LAYER:
        raise ValueError(f"未知数据层级: {layer}")
    return _BY_LAYER[layer]


def layers_as_dict() -> list[dict]:
    return [m.as_dict() for m in LAYERS]


# 归一化置信度：任何基于 L2 的推断都不允许给 high
MAX_CONFIDENCE_BY_LAYER = {L1: "high", L2: "low", L3: "medium"}
CONFIDENCE_LABEL = {"high": "高", "medium": "中", "low": "低"}


def attribution_text(layer: str) -> str:
    m = meta(layer)
    if m.attributable:
        return "可确权到具体主体（季度披露）"
    if layer == L2:
        return "无法归因到具体主体：全市场净申购，不识别申购人"
    return "可确认政策意图，无法确权具体成交"


# 口径术语表（对应研判报告 D1）
GLOSSARY: tuple[dict[str, str], ...] = (
    {"term": "国家队", "definition": "市场惯称，无官方固定名单。本系统按 core / expanded / fx / longterm 四组显式枚举。"},
    {"term": "净申购额", "definition": "ETF 份额增量 × 当日净值（亿元）。仅代表全市场一级市场净申购，无法识别申购人，属 L2 估算。"},
    {"term": "名单进出", "definition": "十大股东名单新进/退出，仅代表排名变化，不等于增持/减持。"},
    {"term": "持股变动", "definition": "经送转股/增发/回购复权调整后的持股数差额；被动变动不计入主动增减持。"},
    {"term": "PIT 时点", "definition": "report_period 为报告期末、disclose_date 为实际披露日、effective_date 为策略最早可用日；回测只用 effective_date 之前的数据。"},
    {"term": "托底信号", "definition": "市场大跌 + 多只核心宽基份额同步跳升 + 官方增持类公告，三项共振时的概率推断；永远 is_confirmed=False。"},
    {"term": "政策底", "definition": "稳定市场力量开始加强的位置；与市场底不等同，历史间隔 40 天至 6 个月。"},
    {"term": "放量倍数", "definition": "当日成交额 ÷ 前 5 日平均成交额（L3 辅助观测）。仅反映二级市场成交活跃度，不改变 ETF 总份额、无法识别买方身份。"},
    {"term": "环比增长率", "definition": "(当日成交额 ÷ 前一交易日成交额 − 1) × 100%。放量 2 倍即环比 ≥ 100%，属客观量能观测，非信号。"},
    {"term": "量能异动", "definition": "放量倍数达到阈值（默认 2.0）的中性观测状态；不等同于份额增加，更不等同于国家队加仓，is_signal 恒为 False。"},
    {"term": "份额 vs 成交额", "definition": "份额由一级市场申赎决定（L2 估算口径）；成交额是二级市场买卖金额。放量不改变份额，二者必须分开看。"},
    {"term": "2026 重点观察", "definition": "对科创50/创业板 ETF 的前端高亮标记，源自未经确权的方向性观点；仅提示关注，不参与信号计算，非推荐。"},
)

# 四个必须规避的数据陷阱（对应研判报告 D2 / D4）
TRAPS: tuple[str, ...] = (
    "十大股东名单「新进/退出」≠「增持/减持」：需结合持股数与总股本变化判定，被挤出前十须单独标记",
    "持股数变动须按送股、转增、配股、增发、回购注销做复权调整，剔除被动变动",
    "「十大股东」与「十大流通股东」分开存储与统计，禁止混用",
    "用披露日而非报告期末做时点（PIT），回测不得前视；季报更正需保留版本链",
)

# 明确屏蔽的噪音（对应《每日监控与进场决策清单》第七节）
NOISE: tuple[str, ...] = (
    "行情软件「主力资金流入 / 国家队资金流向」指标：无法识别买方身份",
    "龙虎榜所谓「国家队席位」、网传「今日买入清单 / 内部名单」",
    "单只 ETF 放量就判定国家队进场：成交额不改变总份额，必须看份额",
    "多只 ETF「放量 2 倍」组合 + 涨跌方向 = 国家队加仓/减仓信号：放量属二级市场成交活跃度，无法识别买方身份",
    "把 ETF 净流入直接表述为「国家队买入 X 亿元」",
    "把未经确权的「持仓占比 20–40%」「2026 布局重心转向科创50/创业板」当成可日频核实的事实或信号权重",
    "「证金概念股 / 王的女人」式跟炒：2015 年证金买入近 400 只，约四成跌破股灾低点",
    "把国家队进场等同于马上满仓：政策底到市场底 40 天至 6 个月不等",
)
