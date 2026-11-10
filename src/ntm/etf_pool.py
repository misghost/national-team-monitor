"""7 只核心宽基 ETF 标的池（结构化、可校验）。

对应《参考策略存档》第一节。设计要点：

1. **结构化而非元组**：原 ``config.CORE_ETF`` 只有 (code, name)，无法承载
   跟踪指数、板块方向、2026 重心等信息；本模块补齐这些字段并保留兼容导出。
2. **事实与观点分离**：代码 / 简称 / 管理人 / 跟踪指数属于可核实事实；
   「国家队持仓占比 20–40%」「2026 年布局重心转向科创50/创业板」属于
   视频提供的**未经确权的参考观点**，放在 ``reference_claims`` 里并标记
   ``claim_verified=False``，绝不写成系统事实或信号判据。
3. **错误修正**：用户材料把 ``159845`` 标为「易方达中证1000ETF」。经
   上交所、新浪财经、中国证券报等多源核对，159845 是**华夏中证1000ETF**
   （华夏基金），易方达那只代码为 159633。本池按核实结果登记。

铁律：标的池只是「观察对象清单」，把某只 ETF 列入池中**不代表**
国家队持有它，更不构成任何操作建议。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

# 板块 / 风格分类
BOARD_LARGE = "大盘蓝筹"
BOARD_BROAD = "全市场核心宽基"
BOARD_MID = "中盘成长"
BOARD_SMALL = "小盘成长"
BOARD_TECH = "科技成长"

# 交易所前缀，用于代码-市场错配校验
_EXCHANGE_BY_CODE = {
    "51": "上交所",
    "58": "上交所",
    "15": "深交所",
    "56": "上交所",
}


@dataclass(frozen=True)
class EtfInstrument:
    code: str
    name: str
    manager: str
    index_name: str
    index_code: str
    board: str
    track_logic: str
    is_2026_focus: bool = False
    is_alternative: bool = False
    role: str = "主标的"
    note: str = ""

    @property
    def exchange(self) -> str:
        return _EXCHANGE_BY_CODE.get(self.code[:2], "未知")

    def as_dict(self) -> dict:
        d = asdict(self)
        d["exchange"] = self.exchange
        return d


ETF_POOL: tuple[EtfInstrument, ...] = (
    EtfInstrument(
        "510050", "上证50ETF华夏（华夏上证50ETF）", "华夏基金",
        "上证50指数", "000016.SH", BOARD_LARGE,
        "大盘蓝筹核心标的，国家队传统重仓品种",
    ),
    EtfInstrument(
        "510300", "沪深300ETF华泰柏瑞（华泰柏瑞沪深300ETF）", "华泰柏瑞基金",
        "沪深300指数", "000300.SH", BOARD_BROAD,
        "全市场核心宽基，国家队持仓规模最大的品种",
    ),
    EtfInstrument(
        "510500", "中证500ETF南方（南方中证500ETF）", "南方基金",
        "中证500指数", "000905.SH", BOARD_MID,
        "中盘成长标的，2025 年前国家队重点布局方向",
    ),
    EtfInstrument(
        "512100", "中证1000ETF南方（南方中证1000ETF）", "南方基金",
        "中证1000指数", "000852.SH", BOARD_SMALL,
        "小盘成长标的，国家队中小盘布局核心品种",
    ),
    EtfInstrument(
        "159845", "中证1000ETF华夏（华夏中证1000ETF）", "华夏基金",
        "中证1000指数", "000852.SH", BOARD_SMALL,
        "中证1000 指数的备选标的，补充流动性",
        is_alternative=True, role="备选标的",
        note="用户材料误标为「易方达中证1000ETF」，经多源核实实为华夏基金产品；"
             "易方达中证1000ETF 代码为 159633，未纳入本池",
    ),
    EtfInstrument(
        "588000", "科创50ETF华夏（华夏科创50ETF）", "华夏基金",
        "科创50指数", "000688.SH", BOARD_TECH,
        "科技成长核心标的，2026 年国家队重点加仓方向（视频观点，未确权）",
        is_2026_focus=True,
    ),
    EtfInstrument(
        "159915", "创业板ETF易方达（易方达创业板ETF）", "易方达基金",
        "创业板指数", "399006.SZ", BOARD_TECH,
        "创业板核心标的，2026 年国家队重点加仓方向（视频观点，未确权）",
        is_2026_focus=True,
    ),
)

_BY_CODE = {e.code: e for e in ETF_POOL}


# ---- 未经确权的参考观点：明确隔离，不参与任何判定 ----
@dataclass(frozen=True)
class ReferenceClaim:
    text: str
    verified: bool = False
    reason: str = ""


REFERENCE_CLAIMS: tuple[ReferenceClaim, ...] = (
    ReferenceClaim(
        "7 只 ETF 是市场上流动性最好、国家队持仓占比最高的品种，"
        "沪深300 / 上证50 / 中证1000 持仓占比普遍在 20%–40%",
        False,
        "ETF 半年报/年报前十大持有人属 L1 季度数据，滞后 1–2 季且仅披露前十名；"
        "「占比 20–40%」无公开日频确权口径，无法核实，不作为系统事实",
    ),
    ReferenceClaim(
        "2026 年之后国家队布局重心从 50/300/500/1000 转向科创50 与创业板",
        False,
        "属方向性判断，无公开数据可日频确权；系统仅以 is_2026_focus 标记"
        "「需在监控中重点关注」，不据此调整信号权重",
    ),
    ReferenceClaim(
        "放量 2 倍视为国家队加仓信号",
        False,
        "成交额放量属二级市场交易活跃度，不改变 ETF 总份额，无法识别买方身份；"
        "列入噪音屏蔽，系统只记录为「量能异动」观测值",
    ),
)

POOL_CAVEAT = (
    "标的池仅为观察对象清单：列入不等于国家队持有该 ETF。"
    "持仓占比与布局重心等说法为未经确权的参考观点（滞后 1–2 季或无法确权），"
    "不构成信号判据；本系统不提供任何标的的操作建议。"
)


def pool_as_dict() -> list[dict]:
    return [e.as_dict() for e in ETF_POOL]


def claims_as_dict() -> list[dict]:
    return [asdict(c) for c in REFERENCE_CLAIMS]


def instrument_by_code(code: str) -> EtfInstrument | None:
    return _BY_CODE.get(str(code).strip())


def pool_codes() -> tuple[str, ...]:
    return tuple(e.code for e in ETF_POOL)


def focus_codes() -> tuple[str, ...]:
    """2026 重点观察对象（视频观点标记，仅用于前端高亮，不参与信号计算）。"""
    return tuple(e.code for e in ETF_POOL if e.is_2026_focus)


def validate_pool(pool: tuple[EtfInstrument, ...] = ETF_POOL) -> list[str]:
    """返回问题列表；空列表表示校验通过。用于自检与单测，避免错配静默入库。"""
    problems: list[str] = []
    seen: set[str] = set()
    for e in pool:
        if not (len(e.code) == 6 and e.code.isdigit()):
            problems.append(f"{e.code}: 代码必须为 6 位数字")
        if e.code in seen:
            problems.append(f"{e.code}: 代码重复")
        seen.add(e.code)
        if e.exchange == "未知":
            problems.append(f"{e.code}: 无法识别交易所前缀")
        if e.code.startswith("15") and e.exchange != "深交所":
            problems.append(f"{e.code}: 15 开头应为深交所")
        if e.code.startswith(("51", "58")) and e.exchange != "上交所":
            problems.append(f"{e.code}: 51/58 开头应为上交所")
        if not e.index_code or "." not in e.index_code:
            problems.append(f"{e.code}: 指数代码格式异常")
        if not e.track_logic.strip():
            problems.append(f"{e.code}: 缺少跟踪逻辑说明")
    return problems


__all__ = [
    "EtfInstrument", "ETF_POOL", "ReferenceClaim", "REFERENCE_CLAIMS",
    "POOL_CAVEAT", "pool_as_dict", "claims_as_dict", "instrument_by_code",
    "pool_codes", "focus_codes", "validate_pool",
    "BOARD_LARGE", "BOARD_BROAD", "BOARD_MID", "BOARD_SMALL", "BOARD_TECH",
]
