"""国家队主体清单与别名归一化。

对应《开发提示词完备性与精准性研判》A1 / A2 / D3：
- 「中国证券金融股份有限公司」=「证金公司」=「中证金公司」，同一实体，不得重复计数；
- 清单覆盖核心 / 扩围 / 外汇局系 / 长线配置四组；
- 长线配置（社保、基本养老）单列，不与托底资金混算；
- 「中投」明确排除。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 分组常量
CORE = "core"            # 核心托底
EXPANDED = "expanded"    # 扩围：2024 后国资运营平台
FX = "fx"                # 外汇局系
LONGTERM = "longterm"    # 长线配置，单列

GROUP_LABEL = {
    CORE: "核心托底",
    EXPANDED: "扩围（国资运营平台）",
    FX: "外汇局系",
    LONGTERM: "长线配置（单列，不与托底资金混算）",
}


@dataclass(frozen=True)
class Entity:
    id: str
    name: str
    group: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""


ENTITIES: tuple[Entity, ...] = (
    # ---- 核心托底 ----
    Entity("huijin", "中央汇金投资有限责任公司", CORE,
           ("中央汇金投资", "中央汇金", "汇金投资", "汇金公司", "中央汇金投资有限责任公司")),
    Entity("huijin_asset", "中央汇金资产管理有限责任公司", CORE,
           ("中央汇金资产管理", "中央汇金资管", "汇金资管", "汇金资产管理")),
    # 关键：所有常见简称收敛到同一个 id，杜绝「中国证券金融公司 + 证金公司」被重复计数
    Entity("csfc", "中国证券金融股份有限公司", CORE,
           ("中国证券金融", "中国证券金融公司", "中国证金", "中证金公司", "中证金",
            "证金公司", "证金", "中国证券金融股份有限公司"),
           note="简称：证金公司 / 中证金公司，与本条为同一实体"),
    Entity("csfc_asset", "证金资管 / 中证金融资产管理计划", CORE,
           ("中证金融资产管理计划", "证金资管", "中证金融资管", "证金资产管理"),
           note="10 只中证金融资管计划，常在十大流通股东中出现"),
    # ---- 扩围 ----
    Entity("guoxin", "中国国新（国新投资 / 国新宏盛等）", EXPANDED,
           ("中国国新", "国新投资", "国新投资有限公司", "国新宏盛", "国新宏盛投资")),
    Entity("chengtong", "中国诚通（诚通金控 / 诚旸投资 / 诚通资本）", EXPANDED,
           ("中国诚通", "诚通金控", "诚旸投资", "诚通资本", "诚通金控投资")),
    # ---- 外汇局系 ----
    Entity("wutongshu", "梧桐树投资平台有限责任公司", FX,
           ("梧桐树", "梧桐树投资", "梧桐树投资平台")),
    Entity("fengshan", "北京凤山投资有限责任公司", FX, ("北京凤山", "凤山投资")),
    Entity("kunteng", "北京坤藤投资有限责任公司", FX, ("北京坤藤", "坤藤投资")),
    # ---- 长线配置（单列） ----
    Entity("ssf", "全国社会保障基金理事会及各社保组合", LONGTERM,
           ("全国社会保障基金", "全国社保基金", "社保基金", "社保基金理事会"),
           note="社保各组合如「全国社保基金一一三组合」亦归入本条"),
    Entity("pension", "基本养老保险基金", LONGTERM,
           ("基本养老保险基金", "基本养老", "养老基金组合")),
)

# 明确不属于 A 股国家队账户的名称，命中后返回 UNKNOWN 且带排除原因
EXCLUDED_NAMES = {
    "中国投资有限责任公司": "中投为主权财富基金，主司境外投资；A 股操作主体为其全资子公司中央汇金",
    "中投": "同上，非 A 股账户主体",
    "中国投资": "同上，非 A 股账户主体",
}

_BY_ID = {e.id: e for e in ENTITIES}
# 最长别名优先匹配，避免「中央汇金」抢先命中「中央汇金资产管理」
_ALIAS_INDEX: tuple[tuple[str, str], ...] = tuple(
    sorted(
        ((alias, e.id) for e in ENTITIES for alias in e.aliases),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
)


def normalize(name: str) -> tuple[str | None, str, str]:
    """把披露文本里的账户名归一化到标准主体。

    返回 (entity_id, group, note)。entity_id 为 None 表示无法归类。
    """
    if not name:
        return None, "", "空名称"
    text = str(name).strip()
    if text in EXCLUDED_NAMES:
        return None, "excluded", EXCLUDED_NAMES[text]
    for alias, entity_id in _ALIAS_INDEX:
        if alias in text:
            e = _BY_ID[entity_id]
            return e.id, e.group, e.note
    return None, "unknown", "未匹配到已知国家队账户，需人工复核"


def entity_by_id(entity_id: str) -> Entity | None:
    return _BY_ID.get(entity_id)


def unique_core_ids() -> set[str]:
    """核心托底主体去重后的 id 集合（用于回归测试：证金不得重复）。"""
    return {e.id for e in ENTITIES if e.group == CORE}


def as_dict() -> list[dict]:
    return [
        {
            "id": e.id,
            "name": e.name,
            "group": e.group,
            "group_label": GROUP_LABEL[e.group],
            "aliases": list(e.aliases),
            "note": e.note,
        }
        for e in ENTITIES
    ]
