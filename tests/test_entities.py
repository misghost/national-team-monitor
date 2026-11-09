"""实体清单回归：修正「证金公司重复」「中投错列」两处事实错误。"""
from ntm.entities import ENTITIES, normalize, unique_core_ids


def test_csfc_aliases_converge_to_single_entity():
    """中国证券金融公司 / 证金公司 / 中证金公司必须是同一实体。"""
    ids = {
        normalize("中国证券金融股份有限公司")[0],
        normalize("中国证券金融公司")[0],
        normalize("证金公司")[0],
        normalize("中证金公司")[0],
        normalize("中国证金")[0],
    }
    assert ids == {"csfc"}


def test_csfc_counted_once_in_core():
    core = [e for e in ENTITIES if e.group == "core"]
    assert sum(1 for e in core if e.name.startswith("中国证券金融")) == 1
    assert "csfc" in unique_core_ids()


def test_cic_excluded():
    eid, group, note = normalize("中国投资有限责任公司")
    assert eid is None
    assert group == "excluded"
    assert "中央汇金" in note or "子公司" in note


def test_longest_alias_wins():
    """汇金资管不能被更短的「中央汇金」抢先匹配。"""
    assert normalize("中央汇金资产管理有限责任公司")[0] == "huijin_asset"
    assert normalize("中央汇金投资有限责任公司")[0] == "huijin"


def test_coverage_of_key_groups():
    groups = {e.group for e in ENTITIES}
    assert {"core", "expanded", "fx", "longterm"} <= groups
    ids = {e.id for e in ENTITIES}
    assert {"guoxin", "chengtong", "wutongshu", "ssf", "pension"} <= ids


def test_unknown_name_reviewable():
    eid, group, note = normalize("某某私募证券投资基金")
    assert eid is None and group == "unknown" and "复核" in note
