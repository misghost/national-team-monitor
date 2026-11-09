"""测试夹具：每个用例使用独立临时数据库，禁用调度器。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NTM_ENABLE_SCHEDULER", "0")
    monkeypatch.setenv("NTM_DEMO_MODE", "1")
    import ntm.config as cfg

    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    yield cfg
