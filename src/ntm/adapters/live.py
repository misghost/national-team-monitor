"""真实数据源适配器（配置驱动，默认禁用）。

设计原则：
- **不内置任何未经核验的第三方接口地址**，端点与凭据全部由环境变量提供；
- 凭据只从环境变量读取，不写入文件、不打印、不进入日志或异常信息；
- 请求失败 / 字段不全 / 数据质量不过关一律抛 LiveDataError 并停止，
  **绝不降级回演示数据**，绝不用合成数据冒充真实数据；
- 所有 L2 记录强制标注 layer=L2、attributable=False，接入真实数据不改变归因边界。

接入清单见 docs/METHODOLOGY.md 第八节；契约见 src/ntm/datasource.py。
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

from .. import config
from ..datasource import LiveDataError, validate_dataset
from .base import BaseAdapter, FetchResult

ENABLED = os.getenv("NTM_ENABLE_LIVE_ADAPTERS", "0").strip().lower() in {"1", "true", "yes"}
NOT_VERIFIED = (
    "真实数据源未核验：接口结构、字段含义、更新频率、授权与版权、"
    "robots/频控/反爬策略均需接入方自行确认。本模块默认禁用。"
)

_last_call = 0.0


class LiveETFShareAdapter(BaseAdapter):
    """通用 HTTP/JSON 真实数据源适配器。

    请求契约（由接入方按已核验的数据源实现）：
        POST {NTM_DATA_ENDPOINT}
        Authorization: Bearer <NTM_DATA_API_KEY>
        Body: {"as_of": "YYYY-MM-DD", "codes": [...7 只 ETF...],
               "required_fields": {...各层必需字段...}}

    响应契约（二选一）：
        {"dataset": {"market": [...], "etf": [...],
                     "announcements": [...], "holdings": [...]}}
        或直接返回上述 dataset 对象。

    etf 每条必须含 total_share / nav / share_change / est_net_yi / source；
    禁止用成交额、放量倍数或「主力资金流入」顶替 total_share。
    """
    name = "live_etf_share"
    verified = False

    def __init__(self, timeout: int | None = None):
        self.timeout = timeout or config.DATA_TIMEOUT
        self.verified = config.data_verified()

    def fetch(self, as_of: str) -> FetchResult:
        if not ENABLED:
            raise LiveDataError(
                "真实数据源适配器默认禁用。设置 NTM_ENABLE_LIVE_ADAPTERS=1 "
                "并完成 docs/METHODOLOGY.md 第八节核验清单后再启用。"
            )

        endpoint = config.data_endpoint()
        key = config.data_api_key()
        if not endpoint or not key:
            raise LiveDataError(
                "真实数据源未配置：需要 NTM_DATA_ENDPOINT 与 NTM_DATA_API_KEY。"
                "系统已停止采集，不会降级到演示数据。"
            )
        if not config.data_verified():
            raise LiveDataError(
                "NTM_DATA_VERIFIED=0：真实数据源的授权、字段口径、更新频率、"
                "频控与合规核验尚未完成。已停止采集，不会降级到演示数据。"
            )

        self._throttle()
        payload = self._request(endpoint, key, as_of)
        dataset = self._extract_dataset(payload)
        problems = validate_dataset(dataset, as_of)
        if problems:
            raise LiveDataError(
                "真实数据质量校验未通过（已丢弃本次采集，不入库、不降级）："
                + "；".join(problems[:12])
            )

        source = str(dataset.get("source") or f"live:{endpoint.split('//')[-1].split('/')[0]}")
        dataset["source"] = source
        dataset["is_demo"] = False
        dataset["as_of"] = as_of

        # 强制归因边界：L2 永远不可归因
        for row in dataset["etf"]:
            row.setdefault("layer", "L2")
            row["attributable"] = False
            row.setdefault("source", source)
        for row in dataset["announcements"]:
            row.setdefault("layer", "L3")
        for row in dataset["holdings"]:
            row.setdefault("layer", "L1")

        return FetchResult(
            dataset=dataset,
            source=source,
            is_demo=False,
            verified=True,
            warnings=[
                "真实数据已接入，但 L2 仍不可归因：无法识别 ETF 申购人身份",
                "所有信号 is_confirmed 恒为 False，本系统不提供买卖建议",
                "真实数据源的授权与合规责任由接入方承担",
            ],
        )

    def _throttle(self) -> None:
        global _last_call
        interval = config.DATA_MIN_INTERVAL
        if interval <= 0:
            return
        wait = interval - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()

    def _request(self, endpoint: str, key: str, as_of: str) -> Any:
        from ..datasource import ANNOUNCEMENT_REQUIRED, ETF_REQUIRED, HOLDINGS_REQUIRED, MARKET_REQUIRED

        body = {
            "as_of": as_of,
            "codes": [code for code, _ in config.CORE_ETF],
            "required_fields": {
                "market": list(MARKET_REQUIRED),
                "etf": list(ETF_REQUIRED),
                "announcements": list(ANNOUNCEMENT_REQUIRED),
                "holdings": list(HOLDINGS_REQUIRED),
            },
        }
        try:
            resp = requests.post(
                endpoint,
                json=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            # 只暴露异常类型，绝不回显 URL / 请求头 / 凭据
            raise LiveDataError(f"真实数据源请求失败（{type(exc).__name__}），已停止采集") from None

        if resp.status_code != 200:
            raise LiveDataError(f"真实数据源返回 HTTP {resp.status_code}，已停止采集")
        try:
            return resp.json()
        except ValueError:
            raise LiveDataError("真实数据源返回非 JSON 内容，已停止采集") from None

    @staticmethod
    def _extract_dataset(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise LiveDataError("真实数据源响应不是 JSON 对象，已停止采集")
        dataset = payload.get("dataset", payload)
        if not isinstance(dataset, dict):
            raise LiveDataError("真实数据源 dataset 不是对象，已停止采集")
        return dict(dataset)


class LiveAnnouncementAdapter(BaseAdapter):
    """官方公告（L3）独立采集器，供后续扩展；同样默认禁用。"""
    name = "live_announcement"
    verified = False

    def fetch(self, as_of: str) -> FetchResult:
        raise LiveDataError(
            "公告采集器待接入：公告解析须保留 "
            "date/source/title/type/is_supportive/url/layer。禁止用新闻标题推断官方公告。"
        )


def probe_connectivity(url: str, timeout: int = 8) -> dict:
    """最小连通性探测，不抓取业务数据、不输出任何凭据。"""
    try:
        r = requests.get(url, timeout=timeout)
        return {"url": url, "status_code": r.status_code, "ok": r.ok}
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "ok": False, "error": type(exc).__name__}


__all__ = [
    "ENABLED", "NOT_VERIFIED", "LiveDataError", "LiveETFShareAdapter",
    "LiveAnnouncementAdapter", "probe_connectivity",
]
