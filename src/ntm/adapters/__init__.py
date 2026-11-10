"""适配器工厂。"""
from __future__ import annotations

from .base import BaseAdapter
from .demo import DemoAdapter


def get_adapter(demo: bool | None = None) -> BaseAdapter:
    from .. import config

    use_demo = config.DEMO_MODE if demo is None else demo
    if use_demo:
        return DemoAdapter()
    from .live import ENABLED, LiveETFShareAdapter

    if not ENABLED:
        raise RuntimeError(
            "NTM_DEMO_MODE=0 但 NTM_ENABLE_LIVE_ADAPTERS 未开启，"
            "且真实数据源尚未核验。请保持演示模式，或完成接入核验后显式启用。"
        )
    if config.data_source() == "wind_mcp":
        from .wind_mcp import WindMCPAdapter

        return WindMCPAdapter()
    return LiveETFShareAdapter()


__all__ = ["get_adapter"]
