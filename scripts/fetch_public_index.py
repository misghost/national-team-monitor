"""抓取宽基指数日线，作为事件研究的行情底座。

数据来源：东方财富公开行情接口 push2his.eastmoney.com（HTTP GET，无凭据）。
口径：不复权日 K，收盘价字段 f53；成交额单位元，成交量单位手。
用途：仅用于「稳市事件后指数前瞻收益 vs 非事件日基准」的可证伪检验。

重要限制：
- 该接口只提供指数行情，**不提供 ETF 总份额**。L2 净申购序列仍需 Wind MCP
  （NTM_WIND_MCP_KEY）或其他已核验数据源，见 docs/METHODOLOGY.md 第八节。
- 本脚本不生成任何「国家队资金」数据，也不把行情字段顶替份额字段。
- 输出目录默认为 data/（已在 .gitignore 中忽略），可用 NTM_DATA_DIR 覆盖。
- 公开接口可能变更；抓取失败时脚本以非零退出，不写入半份数据。

复现：python3 scripts/fetch_public_index.py --code 000300.SH --begin 2015-01-01
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import os

ENDPOINT = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58"  # 日期,开,收,高,低,成交量,成交额,振幅
HEADER = "date,open,close,high,low,volume,amount_yuan,amplitude_pct"


def secid(code: str) -> str:
    """Wind 风格代码转东方财富 secid：沪市 1.，深市 0.。"""
    ticker, _, venue = code.partition(".")
    if venue.upper() == "SZ" or ticker.startswith(("15", "16", "39")):
        return f"0.{ticker}"
    return f"1.{ticker}"


def fetch(code: str, begin: str, end: str, timeout: int = 30) -> list[str]:
    query = urllib.parse.urlencode({
        "secid": secid(code),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": FIELDS2,
        "klt": "101",
        "fqt": "0",
        "beg": begin.replace("-", ""),
        "end": end.replace("-", ""),
        "lmt": "100000",
    })
    req = urllib.request.Request(
        f"{ENDPOINT}?{query}",
        headers={"User-Agent": "Mozilla/5.0 (national-team-monitor research)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    klines = ((payload.get("data") or {}).get("klines")) or []
    rows = []
    for line in klines:
        parts = line.split(",")
        if len(parts) != 8 or not parts[0] or not parts[2]:
            raise ValueError(f"unexpected kline row: {line!r}")
        rows.append(",".join(parts))
    if not rows:
        raise ValueError("empty kline response")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="fetch public index daily kline")
    ap.add_argument("--code", default="000300.SH", help="index code, e.g. 000300.SH")
    ap.add_argument("--begin", default="2015-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    data_dir = Path(os.getenv("NTM_DATA_DIR", str(root / "data")))
    out = Path(args.out) if args.out else data_dir / f"index_{args.code.replace('.', '_').lower()}_daily.csv"

    try:
        rows = fetch(args.code, args.begin, args.end)
    except Exception as exc:  # noqa: BLE001 - 失败即停，不写半份数据
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    tmp.replace(out)
    print(f"wrote {len(rows)} rows -> {out}")
    print(f"range: {rows[0].split(',')[0]} .. {rows[-1].split(',')[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
