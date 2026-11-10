"""真实数据源接入层的契约测试：防伪造、防降级、防凭据泄露。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from ntm import config, datasource
from ntm.adapters import live
from ntm.adapters.demo import DemoAdapter
from ntm.api import app


def _valid_dataset():
    """构造一份字段齐全、口径合规的真实数据样例（数值本身仅用于契约测试）。"""
    etf = [
        {
            "date": "2026-11-10", "code": code, "name": f"ETF-{code}",
            "total_share": 1_000_000.0, "share_change": 2_000.0,
            "nav": 3.5, "est_net_yi": 0.7, "source": "verified-exchange",
        }
        for code in ("510050", "510300", "588000")
    ]
    return {
        "market": [{
            "date": "2026-11-10", "index_close": 3000.0,
            "index_change_pct": -2.0, "index_change_5d_pct": -5.0,
        }],
        "etf": etf,
        "announcements": [{
            "date": "2026-11-09", "source": "中央汇金", "title": "关于增持的公告",
            "type": "增持", "is_supportive": True,
        }],
        "holdings": [{
            "code": "510300", "name": "沪深300ETF", "entity_id": "csfc",
            "entity_name": "中国证券金融股份有限公司", "report_period": "2026Q3",
            "report_period_end": "2026-09-30", "disclose_date": "2026-10-28",
            "effective_date": "2026-10-28", "list_type": "十大流通股东",
            "entry_exit": "unchanged",
        }],
    }


# ---------- 状态接口 ----------

def test_default_status_is_demo():
    s = datasource.current_status()
    assert s["mode"] == "demo"
    assert s["is_demo"] is True
    assert s["ready_to_switch"] is False
    assert s["credential_configured"] is False
    assert len(s["layers"]) == 3
    assert all(not layer["connected"] for layer in s["layers"])
    assert s["layers"][1]["attributable"] is False   # L2 永远不可归因
    assert s["wind_alice_review"]["is_data_source"] is False
    assert s["attribution_note"]


def test_status_never_exposes_credential(monkeypatch):
    secret = "SUPER_SECRET_KEY_19870817"
    monkeypatch.setenv("NTM_DATA_API_KEY", secret)
    s = datasource.current_status()
    assert s["credential_configured"] is True
    assert secret not in json.dumps(s, ensure_ascii=False)
    assert all("未配置 NTM_DATA_API_KEY" not in g for g in s["gaps"])
    monkeypatch.delenv("NTM_DATA_API_KEY")
    s2 = datasource.current_status()
    assert any("NTM_DATA_API_KEY" in g for g in s2["gaps"])


def test_api_datasource_endpoint():
    with TestClient(app) as client:
        r = client.get("/api/datasource")
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "demo"
        assert body["is_demo"] is True
        assert "wind_alice_review" in body
        assert "gaps" in body and body["gaps"]


def test_demo_flag_stays_demo_after_refresh():
    """演示模式刷新后不得被标成真实。"""
    from ntm import db
    from ntm.scheduler import refresh_once
    info = refresh_once("2026-11-09")
    assert info["is_demo"] is True
    assert db.get_meta("dataset_is_demo", "1") == "1"
    assert db.get_meta("dataset_verified", "0") == "0"


# ---------- 数据集契约 ----------

def test_valid_dataset_passes():
    assert datasource.validate_dataset(_valid_dataset(), "2026-11-10") == []


def test_demo_dataset_cannot_be_labelled_real():
    """合成演示数据缺少真实来源标记，必须被闸门拒绝。"""
    problems = datasource.validate_dataset(DemoAdapter().fetch().dataset, "2026-11-09")
    assert problems, "演示数据集不应通过真实数据校验"


def test_rejects_amount_as_share():
    ds = _valid_dataset()
    ds["etf"][0].pop("total_share")
    ds["etf"][0]["amount_yi"] = 88.8
    problems = datasource.validate_dataset(ds, "2026-11-10")
    assert any("不能代替 ETF 总份额" in p for p in problems)


@pytest.mark.parametrize("bad_source", ["demo-generated", "synthetic", "mock", "演示数据"])
def test_rejects_demo_or_synthetic_source(bad_source):
    ds = _valid_dataset()
    for r in ds["etf"]:
        r["source"] = bad_source
    problems = datasource.validate_dataset(ds, "2026-11-10")
    assert any("演示/合成标记" in p for p in problems)


def test_rejects_unknown_etf_code():
    ds = _valid_dataset()
    ds["etf"][0]["code"] = "999999"
    assert any("不在 7 只标的池内" in p for p in datasource.validate_dataset(ds, "2026-11-10"))


def test_rejects_future_date():
    ds = _valid_dataset()
    ds["etf"][0]["date"] = "2026-11-11"
    assert any("前视偏差" in p for p in datasource.validate_dataset(ds, "2026-11-10"))


def test_rejects_attributable_true():
    ds = _valid_dataset()
    ds["etf"][0]["attributable"] = True
    assert any("不得标记为可归因" in p for p in datasource.validate_dataset(ds, "2026-11-10"))


def test_rejects_duplicate_records():
    ds = _valid_dataset()
    ds["etf"].append(dict(ds["etf"][0]))
    assert any("重复记录" in p for p in datasource.validate_dataset(ds, "2026-11-10"))


def test_rejects_missing_layer():
    ds = _valid_dataset()
    del ds["holdings"]
    assert any("缺少数据层 holdings" in p for p in datasource.validate_dataset(ds, "2026-11-10"))


def test_rejects_pit_violation():
    ds = _valid_dataset()
    ds["holdings"][0]["effective_date"] = "2026-10-01"
    assert any("前视偏差" in p for p in datasource.validate_dataset(ds, "2026-11-10"))


# ---------- 真实适配器：绝不降级 ----------

def test_adapter_disabled_by_default(monkeypatch):
    monkeypatch.setattr(live, "ENABLED", False)
    with pytest.raises(datasource.LiveDataError):
        live.LiveETFShareAdapter().fetch("2026-11-10")


def test_adapter_without_credentials_stops(monkeypatch):
    monkeypatch.setattr(live, "ENABLED", True)
    monkeypatch.delenv("NTM_DATA_ENDPOINT", raising=False)
    monkeypatch.delenv("NTM_DATA_API_KEY", raising=False)
    with pytest.raises(datasource.LiveDataError) as exc:
        live.LiveETFShareAdapter().fetch("2026-11-10")
    assert "不会降级到演示数据" in str(exc.value)


def test_adapter_without_verification_stops(monkeypatch):
    monkeypatch.setattr(live, "ENABLED", True)
    monkeypatch.setenv("NTM_DATA_ENDPOINT", "https://verified.example/api")
    monkeypatch.setenv("NTM_DATA_API_KEY", "KEY")
    monkeypatch.setenv("NTM_DATA_VERIFIED", "0")
    with pytest.raises(datasource.LiveDataError) as exc:
        live.LiveETFShareAdapter().fetch("2026-11-10")
    assert "核验" in str(exc.value)


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _enable_live(monkeypatch):
    monkeypatch.setattr(live, "ENABLED", True)
    monkeypatch.setattr(config, "DATA_MIN_INTERVAL", 0.0)
    monkeypatch.setenv("NTM_DATA_ENDPOINT", "https://verified.example/api")
    monkeypatch.setenv("NTM_DATA_API_KEY", "KEY_SHOULD_NEVER_LEAK")
    monkeypatch.setenv("NTM_DATA_VERIFIED", "1")


def test_adapter_success_keeps_attribution_boundary(monkeypatch):
    _enable_live(monkeypatch)
    monkeypatch.setattr(live.requests, "post", lambda *a, **kw: _Resp({"dataset": _valid_dataset()}))
    result = live.LiveETFShareAdapter().fetch("2026-11-10")
    assert result.is_demo is False
    assert result.verified is True
    assert result.dataset["is_demo"] is False
    assert all(r["attributable"] is False for r in result.dataset["etf"])
    assert all(r["layer"] == "L2" for r in result.dataset["etf"])


def test_adapter_rejects_bad_payload(monkeypatch):
    _enable_live(monkeypatch)
    bad = _valid_dataset()
    for r in bad["etf"]:
        r.pop("total_share")
        r["amount_yi"] = 99.0
    monkeypatch.setattr(live.requests, "post", lambda *a, **kw: _Resp({"dataset": bad}))
    with pytest.raises(datasource.LiveDataError) as exc:
        live.LiveETFShareAdapter().fetch("2026-11-10")
    assert "质量校验未通过" in str(exc.value)


def test_adapter_http_error_never_falls_back_and_never_leaks(monkeypatch):
    import requests
    _enable_live(monkeypatch)

    def boom(*a, **kw):
        raise requests.ConnectionError("https://verified.example/api KEY_SHOULD_NEVER_LEAK")

    monkeypatch.setattr(live.requests, "post", boom)
    with pytest.raises(datasource.LiveDataError) as exc:
        live.LiveETFShareAdapter().fetch("2026-11-10")
    assert "KEY_SHOULD_NEVER_LEAK" not in str(exc.value)
    assert "已停止采集" in str(exc.value)
