"""个人节奏分数与 Wind/iFinD 适配器工厂。

不连接真实终端。WindPy / iFinDPy 用假对象或导入失败覆盖。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from ntm import config, db
from ntm.adapters import get_adapter
from ntm.adapters.demo import DemoAdapter
from ntm.adapters.ifind import IFindAdapter
from ntm.adapters.windpy import WindPyAdapter
from ntm.datasource import LiveDataError, validate_dataset
from ntm.personal import personal_score, recent_etf_flows

ROOT = Path(__file__).resolve().parents[1]
SECRET = "ShouldNotLeak-Token-99"
_LITERAL_SECRET = re.compile(
    r"""(?i)(password|passwd|secret|api_key|refresh_token)\s*=\s*(['"])[^'"]+\2"""
)


class _Frame:
    def __init__(self, index, rows):
        self._index = list(index)
        self._rows = list(rows)
        self.empty = not self._rows

    def iterrows(self):
        yield from zip(self._index, self._rows)


class _Wind:
    """最小 WindPy 替身：只实现 wsd / wss 的 usedf=True 返回形态。"""

    def wsd(self, code, fields, begin, end, options, usedf=True):
        as_of = date.fromisoformat(end)
        days = [(as_of - timedelta(days=i)).isoformat() for i in range(9, -1, -1)]
        rows = [{"CLOSE": 3900 + i * 8, "AMT": 1.2e11} for i, _ in enumerate(days)]
        return 0, _Frame(days, rows)

    def wss(self, codes, fields, options, usedf=True):
        index, rows = [], []
        for i, (code, _name) in enumerate(config.CORE_ETF):
            wind = code + (".SH" if code.startswith("5") else ".SZ")
            index.append(wind)
            rows.append({
                "F_UNIT_FLOORTRADING": 20_000_000_000 + i * 1_000_000,
                "UNIT_NAV": 1.2,
                "IOPV": 1.2 + i * 0.01,
            })
        return 0, _Frame(index, rows)


def _load_script():
    path = ROOT / "scripts" / "daily_personal.py"
    spec = importlib.util.spec_from_file_location("daily_personal_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new_sources_have_no_hardcoded_secrets():
    files = [
        ROOT / "src/ntm/adapters/windpy.py",
        ROOT / "src/ntm/adapters/ifind.py",
        ROOT / "src/ntm/personal.py",
        ROOT / "scripts/daily_personal.py",
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert _LITERAL_SECRET.search(text) is None, path.name
        assert SECRET not in text


def test_demo_factory_ignores_live_source(monkeypatch):
    monkeypatch.setenv("NTM_DATA_SOURCE", "windpy")
    monkeypatch.setattr(config, "DEMO_MODE", True)
    assert get_adapter().name == "demo"
    assert isinstance(get_adapter(demo=True), DemoAdapter)


def test_factory_selects_personal_terminals_without_live_switch(monkeypatch):
    import ntm.adapters.live as live

    monkeypatch.setattr(live, "ENABLED", False)
    sys.modules.pop("WindPy", None)
    sys.modules.pop("iFinDPy", None)

    monkeypatch.setenv("NTM_DATA_SOURCE", "WindPy")
    wind = get_adapter(demo=False)
    assert isinstance(wind, WindPyAdapter)
    assert "WindPy" not in sys.modules

    monkeypatch.setenv("NTM_DATA_SOURCE", "IFIND")
    found = get_adapter(demo=False)
    assert isinstance(found, IFindAdapter)
    assert "iFinDPy" not in sys.modules


def test_existing_sources_still_require_live_switch(monkeypatch):
    import ntm.adapters.live as live

    monkeypatch.delenv("NTM_DATA_SOURCE", raising=False)
    monkeypatch.setattr(live, "ENABLED", False)
    with pytest.raises(RuntimeError, match="NTM_ENABLE_LIVE_ADAPTERS"):
        get_adapter(demo=False)

    monkeypatch.setenv("NTM_DATA_SOURCE", "wind_mcp")
    assert get_adapter(demo=False).name == "wind_mcp"
    monkeypatch.setattr(live, "ENABLED", True)
    monkeypatch.delenv("NTM_DATA_SOURCE", raising=False)
    assert get_adapter(demo=False).name == "live_etf_share"


def test_personal_score_empty_db():
    score = personal_score("2026-11-10")
    assert score["score"] == 25
    assert score["level"] == "弱"
    assert score["cum3_yi"] == 0.0
    assert score["cum5_yi"] == 0.0
    assert score["reasons"] == ["暂无显著共振"]
    assert score["latest_signal"] is None


def test_flows_use_aggregated_net_subscription():
    db.init_db()
    fetched = DemoAdapter().fetch("2026-11-09")
    db.save_dataset(fetched.dataset)
    flows = recent_etf_flows(7)
    assert flows
    last = flows[-1]["date"]
    with db.connect() as conn:
        total = conn.execute(
            "SELECT COALESCE(SUM(est_net_yi), 0) FROM etf_daily WHERE date=?",
            (last,),
        ).fetchone()[0]
    assert flows[-1]["net_yi"] == round(total, 2)
    assert flows[-1]["net_yi"] != 0

    score = personal_score("2026-11-09")
    assert 0 <= score["score"] <= 85
    assert score["level"] in {"强", "中", "弱"}
    assert score["latest_signal"]["state_label"]
    assert score["latest_signal"]["is_confirmed"] is False


def test_score_rules_with_mocked_rows(monkeypatch):
    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(db, "get_signals", lambda days=5: [{
        "date": "2026-11-10",
        "state": "INFLOW_ONLY",
        "confidence": "low",
        "is_confirmed": False,
    }])
    monkeypatch.setattr(db, "get_market_row", lambda day: {
        "index_change_pct": -3.0,
        "index_change_5d_pct": -6.0,
    })
    monkeypatch.setattr(db, "get_announcements", lambda limit=8: [])
    flows = [{"date": f"2026-11-{i:02d}", "net_yi": 200.0} for i in range(4, 11)]
    monkeypatch.setattr("ntm.personal.recent_etf_flows", lambda days=7: flows)

    strong = personal_score("2026-11-10")
    assert strong["score"] == 85
    assert strong["level"] == "强"
    assert strong["cum3_yi"] == 600.0
    assert strong["cum5_yi"] == 1000.0
    assert strong["latest_signal"]["state_label"] == "仅观测到增量资金承接"

    monkeypatch.setattr(db, "get_market_row", lambda day: {
        "index_change_pct": 0.2,
        "index_change_5d_pct": 0.4,
    })
    monkeypatch.setattr(db, "get_signals", lambda days=5: [])
    monkeypatch.setattr(
        "ntm.personal.recent_etf_flows",
        lambda days=7: [{"date": "2026-11-10", "net_yi": -360.0}],
    )
    weak = personal_score("2026-11-10")
    assert weak["score"] == 7
    assert weak["level"] == "弱"
    assert any("净赎回" in r for r in weak["reasons"])


def test_windpy_mocked_fetch_passes_gate_and_stores_no_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("NTM_DATA_VERIFIED", "1")
    monkeypatch.setenv("NTM_IFIND_PASS", SECRET)
    monkeypatch.setenv("NTM_DATA_API_KEY", SECRET)
    monkeypatch.setattr(config, "wind_backfill_days", lambda: 4)
    (tmp_path / "verified_announcements.json").write_text(json.dumps([
        {
            "date": "2026-11-09", "source": "中央汇金", "title": "增持 ETF",
            "type": "增持", "is_supportive": True,
        },
        {"date": "2026-11-09", "title": "缺字段，应跳过"},
    ], ensure_ascii=False), encoding="utf-8")

    adapter = WindPyAdapter(history_path=tmp_path / "windpy_history.json")
    monkeypatch.setattr(adapter, "_connect", lambda: _Wind())
    result = adapter.fetch("2026-11-10")

    assert result.is_demo is False
    assert result.verified is True
    assert validate_dataset(result.dataset, "2026-11-10") == []
    assert result.dataset["etf"]
    assert all(row["attributable"] is False for row in result.dataset["etf"])
    assert all(row["layer"] == "L2" for row in result.dataset["etf"])
    assert len(result.dataset["announcements"]) == 1
    history = (tmp_path / "windpy_history.json").read_text(encoding="utf-8")
    blob = history + json.dumps(result.dataset, ensure_ascii=False)
    assert SECRET not in blob
    assert SECRET not in " ".join(result.warnings)


def test_windpy_missing_sdk_is_explicit(monkeypatch):
    monkeypatch.setenv("NTM_DATA_VERIFIED", "1")
    monkeypatch.setenv("NTM_IFIND_PASS", SECRET)

    import builtins
    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "WindPy":
            raise ImportError("blocked")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(LiveDataError) as exc:
        WindPyAdapter().fetch("2026-11-10")
    message = str(exc.value)
    assert "WindPy" in message
    assert SECRET not in message


def test_ifind_unverified_stops_before_sdk(monkeypatch):
    monkeypatch.delenv("NTM_DATA_VERIFIED", raising=False)
    monkeypatch.setenv("NTM_IFIND_USER", "user-demo")
    monkeypatch.setenv("NTM_IFIND_PASS", SECRET)
    seen = []

    import builtins
    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "iFinDPy":
            seen.append(name)
            raise ImportError("blocked")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)
    adapter = IFindAdapter()
    with pytest.raises(LiveDataError) as exc:
        adapter.fetch("2026-11-10")
    assert "核验" in str(exc.value)
    assert SECRET not in str(exc.value)
    assert seen == []
    assert all(SECRET not in str(value) for value in adapter.__dict__.values())


def test_ifind_sdk_missing_and_token_path_do_not_leak(monkeypatch):
    monkeypatch.setenv("NTM_DATA_VERIFIED", "1")
    monkeypatch.delenv("NTM_IFIND_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("ifind_api_refresh_token", raising=False)
    monkeypatch.delenv("IFIND_API_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("ifind_api_access_token", raising=False)
    monkeypatch.delenv("IFIND_API_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("NTM_IFIND_USER", "user-demo")
    monkeypatch.setenv("NTM_IFIND_PASS", SECRET)

    import builtins
    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "iFinDPy":
            raise ImportError("blocked")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(LiveDataError) as exc:
        IFindAdapter().fetch("2026-11-10")
    assert "iFinDPy" in str(exc.value)
    assert SECRET not in str(exc.value)

    monkeypatch.delenv("NTM_IFIND_USER", raising=False)
    monkeypatch.delenv("NTM_IFIND_PASS", raising=False)
    monkeypatch.setenv("NTM_IFIND_REFRESH_TOKEN", SECRET)
    with pytest.raises(LiveDataError) as exc2:
        IFindAdapter().fetch("2026-11-10")
    message = str(exc2.value)
    assert "TODO" in message
    assert "NTM_IFIND_SHARE_FIELD" in message
    assert SECRET not in message


def test_public_error_redacts_env_secrets(monkeypatch):
    monkeypatch.setenv("NTM_IFIND_PASS", SECRET)
    module = _load_script()
    text = module._public_error(RuntimeError(f"login failed for {SECRET}"))
    assert SECRET not in text
    assert "***" in text


def test_daily_personal_demo_without_optional_dependencies(tmp_path):
    env = os.environ.copy()
    for key in (
        "NTM_WECHAT_WEBHOOK", "NTM_SMTP_HOST", "NTM_SMTP_TO",
        "NTM_DATA_API_KEY", "NTM_IFIND_USER", "NTM_IFIND_PASS",
        "NTM_IFIND_REFRESH_TOKEN", "NTM_DATA_SOURCE",
    ):
        env.pop(key, None)
    env.update({
        "NTM_DEMO_MODE": "1",
        "NTM_DATA_VERIFIED": "0",
        "NTM_ENABLE_SCHEDULER": "0",
        "NTM_DB_PATH": str(tmp_path / "ntm.db"),
        "NTM_DATA_DIR": str(tmp_path),
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONIOENCODING": "utf-8",
    })
    code = (
        "import sys\n"
        "sys.modules['requests'] = None\n"
        "sys.modules['WindPy'] = None\n"
        "sys.modules['iFinDPy'] = None\n"
        "import runpy\n"
        "runpy.run_path('scripts/daily_personal.py', run_name='__main__')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "关注强度" in proc.stdout
    assert "is_demo=True" in proc.stdout
    assert SECRET not in proc.stdout + proc.stderr
