"""全局配置。

所有可变项均可通过环境变量覆盖。凭据只从环境变量读取，绝不写入文件、
不打印、不进入日志或报告（见 docs/METHODOLOGY.md 合规章节）。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("NTM_DATA_DIR", str(ROOT / "data")))
DB_PATH = Path(os.getenv("NTM_DB_PATH", str(DATA_DIR / "ntm.db")))
STATIC_DIR = Path(__file__).resolve().parent / "static"

VERSION = "1.0.0"
APP_NAME = "国家队资金三层监控看板"

# 默认使用演示数据。真实数据源必须显式开启并自行完成合规与可用性核验。
DEMO_MODE = os.getenv("NTM_DEMO_MODE", "1").strip().lower() not in {"0", "false", "no", "off"}

HOST = os.getenv("NTM_HOST", "0.0.0.0")
PORT = int(os.getenv("NTM_PORT", "8000"))
LOG_LEVEL = os.getenv("NTM_LOG_LEVEL", "info")

# ---- 三重共振判定阈值（可按市场波动水平调整） ----
MARKET_DROP_1D_PCT = float(os.getenv("NTM_MARKET_DROP_1D", "-1.5"))
MARKET_DROP_5D_PCT = float(os.getenv("NTM_MARKET_DROP_5D", "-4.0"))
ETF_SYNC_MIN = int(os.getenv("NTM_ETF_SYNC_MIN", "3"))
ETF_NET_MIN_YI = float(os.getenv("NTM_ETF_NET_MIN_YI", "200"))
OFFICIAL_LOOKBACK_DAYS = int(os.getenv("NTM_OFFICIAL_LOOKBACK", "5"))

# ---- 调度 ----
REFRESH_HOUR = int(os.getenv("NTM_REFRESH_HOUR", "16"))   # 每交易日盘后
REFRESH_MINUTE = int(os.getenv("NTM_REFRESH_MINUTE", "30"))

# ---- 预警（仅从环境读取） ----
WECHAT_WEBHOOK = os.getenv("NTM_WECHAT_WEBHOOK", "")
SMTP_HOST = os.getenv("NTM_SMTP_HOST", "")
SMTP_TO = os.getenv("NTM_SMTP_TO", "")
ALERT_MAX_PER_DAY = int(os.getenv("NTM_ALERT_MAX_PER_DAY", "3"))

# ---- 量能观察层（客观观测，不参与三重共振判定） ----
VOLUME_SPIKE_MULTIPLE = float(os.getenv("NTM_VOLUME_SPIKE_MULTIPLE", "2.0"))
VOLUME_MA_WINDOW = int(os.getenv("NTM_VOLUME_MA_WINDOW", "5"))

# 核心宽基 ETF 观察池：7 只，结构化定义见 etf_pool.py。
# 保留 (code, name) 二元组形式以兼容既有 demo 适配器与数据契约。
from .etf_pool import ETF_POOL as _ETF_POOL  # noqa: E402  避免模块级循环导入

CORE_ETF = tuple((e.code, e.name) for e in _ETF_POOL)

# 明确排除：不是 A 股国家队账户主体
EXCLUDED_ENTITIES = (
    "中国投资有限责任公司",  # 中投：主权财富基金，主司境外投资；A股主体为其子公司中央汇金
    "证券投资者保护基金",
)

DISCLAIMER = (
    "本系统所有「国家队资金」判断均为基于公开数据的概率推断，无法确权；"
    "数据分 L1 实测 / L2 估算 / L3 代理三层，L2 仅代表全市场一级市场净申购、无法识别申购人。"
    "系统不提供任何个股买卖建议，不构成投资建议或择时建议。市场有风险，决策需谨慎。"
)
