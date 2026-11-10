"""7 只宽基 ETF 标的池：完整性、错误修正、未确权观点隔离、校验器。"""
from ntm import config
from ntm.etf_pool import (
    ETF_POOL, REFERENCE_CLAIMS, focus_codes, instrument_by_code,
    pool_as_dict, pool_codes, validate_pool,
)

EXPECTED_CODES = ("510050", "510300", "510500", "512100", "159845", "588000", "159915")


def test_pool_has_exactly_7_instruments():
    assert len(ETF_POOL) == 7
    assert pool_codes() == EXPECTED_CODES
    assert len(set(pool_codes())) == 7


def test_159845_corrected_to_huaxia():
    """用户材料误标易方达，核实后应为华夏中证1000ETF。"""
    e = instrument_by_code("159845")
    assert e is not None
    assert "华夏" in e.name
    assert "易方达" not in e.name
    assert e.manager == "华夏基金"
    assert e.index_code == "000852.SH"
    assert e.is_alternative is True
    assert "159633" in e.note  # 易方达真实代码留痕


def test_exchange_matches_code_prefix():
    for e in ETF_POOL:
        if e.code.startswith("15"):
            assert e.exchange == "深交所"
        else:
            assert e.exchange == "上交所"


def test_2026_focus_only_tech():
    assert set(focus_codes()) == {"588000", "159915"}


def test_reference_claims_are_unverified():
    assert len(REFERENCE_CLAIMS) == 3
    assert all(c.verified is False for c in REFERENCE_CLAIMS)
    assert all(c.reason for c in REFERENCE_CLAIMS)


def test_validator_passes_default_pool():
    assert validate_pool() == []


def test_validator_catches_duplicate_and_bad_code():
    from dataclasses import replace
    from ntm.etf_pool import EtfInstrument

    good = ETF_POOL[0]
    dup = replace(good)                 # 与 good 同代码 → 触发重复检测
    bad_code = replace(good, code="ABC")
    problems = validate_pool((good, dup, bad_code))
    assert any("重复" in p for p in problems)
    assert any("6 位数字" in p for p in problems)


def test_config_core_etf_derived_from_pool():
    """config.CORE_ETF 保持 (code, name) 二元组兼容，且与标的池同源。"""
    assert len(config.CORE_ETF) == 7
    assert tuple(c for c, _ in config.CORE_ETF) == EXPECTED_CODES
    assert all(isinstance(n, str) and n for _, n in config.CORE_ETF)


def test_pool_as_dict_has_full_schema():
    d = pool_as_dict()
    assert len(d) == 7
    assert {"code", "name", "manager", "index_name", "index_code",
            "board", "track_logic", "is_2026_focus", "is_alternative",
            "role", "note", "exchange"} <= set(d[0])
