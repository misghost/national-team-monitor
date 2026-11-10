"""个人每日节奏观察脚本。

用法示例（本机已登录 Wind）：
  NTM_DEMO_MODE=0 NTM_DATA_SOURCE=windpy NTM_DATA_VERIFIED=1 \\
    PYTHONPATH=src python3 scripts/daily_personal.py

iFinD 示例：
  NTM_DEMO_MODE=0 NTM_DATA_SOURCE=ifind NTM_DATA_VERIFIED=1 \\
    NTM_IFIND_USER=xxx NTM_IFIND_PASS=xxx \\
    PYTHONPATH=src python3 scripts/daily_personal.py

演示模式（无需 Wind / iFinD）：
  NTM_DEMO_MODE=1 PYTHONPATH=src python3 scripts/daily_personal.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ntm import db  # noqa: E402
from ntm.personal import personal_score  # noqa: E402
from ntm.scheduler import refresh_once  # noqa: E402

# 这些环境变量的值一旦出现在异常文本里，打印前打掉。只比较、不记录原文。
_SECRET_ENV = (
    "NTM_IFIND_USER",
    "NTM_IFIND_PASS",
    "NTM_IFIND_REFRESH_TOKEN",
    "NTM_DATA_API_KEY",
    "NTM_WIND_MCP_KEY",
    "NTM_WECHAT_WEBHOOK",
    "ifind_api_refresh_token",
    "IFIND_API_REFRESH_TOKEN",
    "ifind_api_access_token",
    "IFIND_API_ACCESS_TOKEN",
)


def _public_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for key in _SECRET_ENV:
        secret = os.getenv(key) or ""
        if secret and secret in text:
            text = text.replace(secret, "***")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="国家队节奏个人日报")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--no-refresh", action="store_true")
    args = parser.parse_args()

    db.init_db()

    if not args.no_refresh:
        print("正在刷新数据...")
        try:
            info = refresh_once(args.as_of)
            print(f"数据源: {info.get('source')}  is_demo={info.get('is_demo')}")
        except Exception as exc:
            print(f"刷新失败: {_public_error(exc)}", file=sys.stderr)
            print("将尝试使用已有数据继续。")

    s = personal_score(args.as_of)

    print("\n" + "=" * 56)
    print(f"国家队节奏观察  {s['as_of']}")
    print("=" * 56)
    print(f"关注强度: {s['score']} / 85   （{s['level']}）")
    print(f"近3日核心宽基净申购: {s['cum3_yi']:+.1f} 亿")
    print(f"近5日核心宽基净申购: {s['cum5_yi']:+.1f} 亿")

    print("\n依据:")
    for r in s["reasons"]:
        print(f"  · {r}")

    print("\n近7日明细（亿元）:")
    for f in s.get("recent_flows") or []:
        sign = "+" if f["net_yi"] >= 0 else ""
        print(f"  {f['date']}  {sign}{f['net_yi']:,.1f}")

    if s.get("latest_signal"):
        sig = s["latest_signal"]
        print(f"\n系统原始信号: {sig.get('state_label')}  置信度={sig.get('confidence')}")

    print("\n" + s["note"])
    print("=" * 56)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
