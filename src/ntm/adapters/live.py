"""真实数据源适配器（骨架，默认禁用）。

诚实声明：本模块的接口地址、字段结构与可用性**尚未经过验证**，
且接入前需自行完成授权、版权、频控与合规核验。默认不启用，
启用后若请求失败会明确报错，绝不用合成数据冒充真实数据。

接入清单见 docs/METHODOLOGY.md「真实数据源接入」章节。
"""
from __future__ import annotations

import os

import requests

from .base import BaseAdapter, FetchResult

ENABLED = os.getenv("NTM_ENABLE_LIVE_ADAPTERS", "0").strip().lower() in {"1", "true", "yes"}
NOT_VERIFIED = (
    "真实数据源未核验：接口结构、字段含义、更新频率、授权与版权、"
    "robots/频控/反爬策略均需接入方自行确认。本模块默认禁用。"
)


class LiveETFShareAdapter(BaseAdapter):
    """宽基 ETF 每日总份额（L2 估算源）。

    建议核验顺序：
      1. 交易所官网 / 基金公司官网的每日份额披露（最权威）；
      2. 东方财富、同花顺等公开页面的 ETF 份额字段；
      3. 商业数据接口（Wind / iFinD，需授权）。
    仅取「总份额」与「净值」，**不得**把「主力资金流入」当作国家队数据。
    """
    name = "live_etf_share"
    verified = False

    def __init__(self, timeout: int = 10):
        self.timeout = timeout

    def fetch(self, as_of: str) -> FetchResult:
        if not ENABLED:
            raise NotImplementedError(
                "真实 ETF 份额适配器默认禁用。设置 NTM_ENABLE_LIVE_ADAPTERS=1 "
                "并完成 docs/METHODOLOGY.md 的核验清单后再启用。"
            )
        # 接入点留白：核验后按实际接口实现解析，返回值必须带
        # layer='L2'、source、并标注 attributable=False。
        raise NotImplementedError(
            "待接入：请按已核验的接口实现 ETF 总份额解析。"
            "返回记录须包含 date/code/name/total_share/share_change/nav/est_net_yi/layer/source。"
        )


class LiveAnnouncementAdapter(BaseAdapter):
    """官方公告（L3）。

    建议信源：中国人民银行、中央汇金、证监会、国资委、
    中国国新 / 中国诚通官网、巨潮资讯网、交易所公告。
    """
    name = "live_announcement"
    verified = False

    def fetch(self, as_of: str) -> FetchResult:
        if not ENABLED:
            raise NotImplementedError("真实公告适配器默认禁用（NTM_ENABLE_LIVE_ADAPTERS=0）")
        raise NotImplementedError(
            "待接入：公告解析须保留 date/source/title/type/is_supportive/url/layer。"
        )


def probe_connectivity(url: str, timeout: int = 8) -> dict:
    """最小连通性探测，不抓取业务数据、不输出任何凭据。"""
    try:
        r = requests.get(url, timeout=timeout)
        return {"url": url, "status_code": r.status_code, "ok": r.ok}
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "ok": False, "error": type(exc).__name__}


__all__ = [
    "ENABLED", "NOT_VERIFIED", "LiveETFShareAdapter",
    "LiveAnnouncementAdapter", "probe_connectivity",
]
