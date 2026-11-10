"""适配器工厂。"""
from __future__ import annotations

from .base import BaseAdapter
from .demo import DemoAdapter


def get_adapter(demo: bool | None = None) -> BaseAdapter:
    """选择数据适配器。

    演示模式优先，忽略 ``NTM_DATA_SOURCE``。
    ``windpy`` / ``ifind`` / ``wind_mcp`` 由数据源名称直接选择。
    未指定来源的通用 HTTP 适配器仍须 ``NTM_ENABLE_LIVE_ADAPTERS``。
    """
    from .. import config

    use_demo = config.DEMO_MODE if demo is None else demo
    if use_demo:
        return DemoAdapter()

    source = config.data_source()
    if source == "windpy":
        from .windpy import WindPyAdapter
        return WindPyAdapter()
    if source == "ifind":
        from .ifind import IFindAdapter
        return IFindAdapter()
    if source == "wind_mcp":
        from .wind_mcp import WindMCPAdapter
        return WindMCPAdapter()

    # 既有路径：保持「未显式启用则拒绝」的行为，不因新增个人终端适配器而放宽。
    from .live import ENABLED, LiveETFShareAdapter

    if not ENABLED:
        raise RuntimeError(
            "NTM_DEMO_MODE=0 但 NTM_ENABLE_LIVE_ADAPTERS 未开启，"
            "且真实数据源尚未核验。请保持演示模式，或完成接入核验后显式启用。"
        )
    return LiveETFShareAdapter()


__all__ = ["get_adapter"]
