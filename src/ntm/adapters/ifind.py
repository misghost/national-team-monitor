"""iFinD 适配器（个人使用）。

支持两种方式（优先 Python SDK）：
1. iFinDPy 本地登录（THS_iFinDLogin）
2. 通过环境变量配置的 HTTP API（refresh_token）

取数口径与 Wind 适配器保持一致：
- 指数：历史行情
- ETF 场内流通份额（字段需用户在超级命令确认后通过环境变量覆盖）
- 净值用于计算估算净申购额

凭据只从环境变量读取，绝不硬编码、不打印、不落盘，也不挂到实例属性上。
iFinDPy / requests 均懒加载：未选择 ifind 时，缺少这两个包不影响导入与演示模式。
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .. import config
from ..datasource import LiveDataError, validate_dataset  # noqa: F401  解析补齐后必须过这道闸门
from .base import BaseAdapter, FetchResult

SOURCE_NAME = "ifind"
INDEX_CODE = "000300.SH"
HISTORY_FILENAME = "ifind_history.json"

# 常见份额字段候选（iFinD 命名可能不同，优先用环境变量覆盖）
SHARE_FIELD_CANDIDATES = (
    "ths_unit_floortrading_fund",
    "ths_fund_unit_floortrading",
    "unit_floortrading",
    "ths_fund_shares_fund",
)


def _ifind_code(code: str) -> str:
    return code + (".SH" if code.startswith("5") else ".SZ")


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


class IFindAdapter(BaseAdapter):
    """iFinD 数据适配器。"""

    name = "ifind"
    verified = True

    def __init__(self, history_path: Path | None = None):
        self.history_path = history_path or (config.DATA_DIR / HISTORY_FILENAME)
        self.pool = {code: name for code, name in config.CORE_ETF}
        self.backfill_days = config.wind_backfill_days()

    def _require_requests(self):
        try:
            import requests
        except ImportError as exc:
            raise LiveDataError(
                "未安装 requests，无法使用 iFinD HTTP refresh_token 方式。"
            ) from exc
        return requests

    def _connect_sdk(self, username: str, password: str):
        try:
            from iFinDPy import THS_BasicData, THS_HistoryQuotes, THS_iFinDLogin
            self._THS_HistoryQuotes = THS_HistoryQuotes
            self._THS_BasicData = THS_BasicData
        except ImportError as exc:
            raise LiveDataError("未安装 iFinDPy。请确认已安装同花顺量化接口") from exc

        if username and password:
            ret = THS_iFinDLogin(username, password)
            if ret != 0:
                # 只报告返回码，不回显账号或密码
                raise LiveDataError(f"iFinD 登录失败，返回码 {ret}")
        # 若已在终端登录，部分环境可不显式登录
        return True

    def _share_field(self) -> str:
        return _env("NTM_IFIND_SHARE_FIELD") or SHARE_FIELD_CANDIDATES[0]

    def _load_history(self) -> dict[str, Any]:
        if self.history_path.exists():
            try:
                data = json.loads(self.history_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("days"), dict):
                    return data
            except (OSError, ValueError):
                pass
        return {"version": 1, "days": {}}

    def _save_history(self, history: dict[str, Any]) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.history_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.history_path)

    def _fetch_kline_sdk(self, as_of: str) -> list[dict[str, Any]]:
        end = date.fromisoformat(as_of)
        begin = end - timedelta(days=config.wind_kline_days())
        # THS_HistoryQuotes 返回格式需根据实际 SDK 调整；这里做兼容处理
        result = self._THS_HistoryQuotes(
            INDEX_CODE,
            "open;high;low;close;volume;amount",
            "Interval:D,Fill:Previous",
            begin.isoformat(),
            end.isoformat(),
        )
        # 实际返回结构因版本而异。未识别的结构直接停止，不用空数据冒充成功。
        if not result or (hasattr(result, "errorcode") and result.errorcode != 0):
            raise LiveDataError("iFinD 指数行情取数失败")
        raise LiveDataError(
            "iFinD SDK 返回格式需根据你的终端版本调整解析逻辑。"
            "建议先用超级命令确认字段后修改 _fetch_kline_sdk。"
        )

    def fetch(self, as_of: str | None = None) -> FetchResult:
        as_of = as_of or date.today().isoformat()
        try:
            date.fromisoformat(as_of)
        except (TypeError, ValueError):
            raise LiveDataError(f"as_of 不是合法 ISO 日期：{as_of}") from None
        if not config.data_verified():
            raise LiveDataError(
                "NTM_DATA_VERIFIED=0：数据源核验清单未完成。已停止采集，不会降级到演示数据。"
            )

        username = _env("NTM_IFIND_USER")
        password = _env("NTM_IFIND_PASS")
        token = _env("NTM_IFIND_REFRESH_TOKEN")
        # 仅在没有 SDK 账号、只给了 refresh_token 时走 HTTP。解析尚未完成，明确失败。
        if token and not (username and password):
            self._require_requests()
            raise LiveDataError(
                "已检测到 iFinD refresh_token 方式，但 HTTP 响应解析尚未实现。"
                "请改用 iFinDPy（设置 NTM_IFIND_USER 与 NTM_IFIND_PASS），"
                "或按终端实际返回完善 adapters/ifind.py。"
                "令牌不会写入日志或磁盘。"
            )

        try:
            self._connect_sdk(username, password)
            # 登录成功也不要假装取到了数：字段与返回结构必须按终端核对后再解析。
            raise LiveDataError(
                "iFinD 适配器骨架已就绪。请在终端用超级命令确认"
                "「场内流通份额」对应字段名，设置 NTM_IFIND_SHARE_FIELD，"
                "并视需要调整 _fetch_kline_sdk / 份额解析逻辑。"
                "完成前不会入库，也不会降级到演示数据。"
            )
        except LiveDataError:
            raise
        except Exception as exc:
            raise LiveDataError(f"iFinD 连接异常：{type(exc).__name__}") from None
