# 数据字典与 API 契约

## 一、数据库表（SQLite）

### `market_daily` — 市场日频（L3 辅助）
| 字段 | 类型 | 说明 |
|------|------|------|
| date | TEXT PK | ISO 交易日 |
| index_close | REAL | 指数收盘 |
| index_change_pct | REAL | 当日涨跌幅 % |
| index_change_5d_pct | REAL | 近 5 日累计涨跌幅 % |
| amount_yi | REAL | 成交额（亿元） |
| margin_balance_yi | REAL | 融资余额（亿元） |

### `etf_daily` — 宽基 ETF 每日份额明细（L2）+ 成交额（L3 辅助）
| 字段 | 类型 | 说明 |
|------|------|------|
| date, code | TEXT PK | 交易日、ETF 代码（7 只，见 `etf_pool.py`） |
| name | TEXT | ETF 名称 |
| total_share | REAL | **总份额**（不是成交额） |
| share_change | REAL | 份额增量 |
| nav | REAL | 当日净值 |
| est_net_yi | REAL | 估算净申购额（亿元）= share_change × nav / 1e8 |
| amount_yi | REAL | **成交额（亿元）**，二级市场买卖金额；`NOT NULL DEFAULT 0` |
| layer | TEXT | 恒为 `L2` |
| source | TEXT | 数据来源标识 |

> **两条口径必须分开**：`est_net_yi` 来自份额变化（一级市场申赎，L2 估算、不可归因）；
> `amount_yi` 是二级市场成交额（L3 辅助观测）。**放量不改变份额**，
> 因此不得用 `amount_yi` 推导申购额或国家队买入额。
>
> `attributable=False` 是模型层面的硬约束，不依赖字段表达。
>
> 旧库兼容：`init_db()` 中的 `_migrate()` 用 `PRAGMA table_info` 检测，
> 缺 `amount_yi` 时执行 `ALTER TABLE ... ADD COLUMN`，因为
> `CREATE TABLE IF NOT EXISTS` 不会为已存在的表补列。

### `etf_pool`（代码常量，非数据库表）
7 只标的结构化定义见 `src/ntm/etf_pool.py`，含 code / name / manager /
index_name / index_code / board / track_logic / is_2026_focus / is_alternative /
role / note。`validate_pool()` 保证代码唯一、6 位数字、交易所前缀匹配。

未经确权的参考观点单独存于 `REFERENCE_CLAIMS`（`verified=False`），
与可核实事实物理隔离，不参与任何判定。

### `announcements` — 官方公告（L3）
date / source / title / type / is_supportive(0|1) / url / layer(恒 `L3`)，
UNIQUE(date, source, title)。

### `holdings` — 季度持仓确权（L1）
| 字段 | 说明 |
|------|------|
| code, name | 股票代码与名称 |
| entity_id, entity_name | 归一化后的标准主体 |
| report_period | 季度标签，如 `2026Q3`（**不是日期**） |
| report_period_end | 报告期末 ISO 日期，如 `2026-09-30` |
| disclose_date | 实际披露日 |
| effective_date | PIT 最早可用日 = 披露日 |
| shares, float_pct | 持股数、占流通股比例 |
| list_type | `十大股东` / `十大流通股东`（禁止跨口径比较） |
| entry_exit | 变动分类，含「名单新进（不可判定为增持）」等 |
| passive_change | 是否纯被动变动 |
| note, layer | 提示与层级（恒 `L1`） |

### `signals` — 每日信号
date PK / state / confidence / is_confirmed(恒 0) / payload(JSON)。

### `alerts` / `meta`
预警投递记录、运行期元数据（`dataset_is_demo`、`dataset_source`）。

---

## 二、API 契约

所有响应 `is_demo=true` 表示演示数据。前端按此契约实现。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | `{status, version, is_demo}` |
| GET | `/api/overview` | 当前信号 + 三层说明 + 免责 |
| GET | `/api/etf/shares?days=30` | ETF 净申购序列 + 指数 |
| GET | `/api/etf/pool` | 7 只标的池 + 未确权观点 + `validation` 校验结果 |
| GET | `/api/etf/momentum?day=YYYY-MM-DD` | 量能观测（成交额/环比/放量倍数/份额对照），`summary.is_signal` 恒 `false` |
| GET | `/api/reference/strategy` | 参考策略原文存档与逐条处置（含不实现理由） |
| GET | `/api/signals?days=90` | 历史信号 |
| GET | `/api/announcements?limit=10` | 公告（L3） |
| GET | `/api/holdings?limit=20` | 季度持仓（L1） |
| GET | `/api/methodology` | 三层模型/术语表/陷阱/噪音/主体清单 |
| GET | `/api/backtest?horizon=20` | 回测结果 |
| GET | `/api/alerts` | 预警投递状态 |
| POST | `/api/refresh?as_of=YYYY-MM-DD` | 刷新数据并重算信号 |

### `GET /api/overview` 示例
```json
{
  "as_of": "2026-11-09",
  "is_demo": true,
  "signal": {
    "date": "2026-11-09",
    "state": "POLICY_BOTTOM_PROBABLE",
    "state_label": "政策底信号（推断，非实锤）",
    "confidence": "medium",
    "confidence_label": "中",
    "is_confirmed": false,
    "attribution": "无法归因到具体主体：全市场净申购，不识别申购人",
    "summary": "三重共振成立……但仍是推断而非实锤",
    "conditions": {
      "market_drop": {"met": true, "label": "...", "detail": "..."},
      "etf_sync":    {"met": true, "label": "...", "detail": "..."},
      "official":    {"met": true, "label": "...", "detail": "..."}
    },
    "caveat": "政策底不等于市场底……不构成投资建议"
  },
  "layers": [
    {"layer": "L1", "name": "实测（季度确权）", "timeliness": "滞后 1–2 季",
     "attributable": true, "confidence": "high", "sources": "...", "rule": "..."}
  ],
  "disclaimer": "..."
}
```

### `GET /api/etf/shares` items 字段
`date` / `index_close` / `index_change_pct` / `net_subscription_yi` /
`sync_etf_count` / `note`（恒含「L2 估算·不可归因」）。

### `GET /api/etf/momentum` 契约
```json
{
  "date": "2026-11-09", "is_demo": true,
  "pool": ["7 只标的完整对象"], "pool_caveat": "...",
  "items": [{
    "code": "588000", "name": "...",
    "amount_yi": 120.0, "amount_prev_yi": 45.0, "amount_ma_yi": 40.0,
    "amount_change_yi": 75.0, "amount_change_pct": 166.67,
    "volume_multiple": 3.0, "is_spike": true, "is_2026_focus": true,
    "share_change_yi": 60.0, "note": "L3 辅助观测 · 不可归因"
  }],
  "summary": {
    "date": "2026-11-09", "total_etf": 7, "spike_count": 7,
    "focus_spike_count": 2, "threshold": 2.0, "spike_codes": ["..."],
    "observation": "...（纯观测，含份额同步/背离对照）",
    "caveat": "...", "is_signal": false
  },
  "caveat": "..."
}
```
`volume_multiple = amount_yi / 前 5 日平均成交额`（基线为 0 时返回 0）。
**`is_signal` 恒为 `false`**：量能观测不进入 `signals.evaluate()`，
三重共振条件集合恒为 `{market_drop, etf_sync, official}`。

### `GET /api/reference/strategy` 契约
`{version, title, statement, items[], disposition_counts}`；每项含
`section / title / original / disposition / reason / implementation`。
`disposition` 枚举：`已实现` / `保留为参考` / `不实现（合规）`。

### 信号状态枚举
| state | 含义 |
|-------|------|
| `POLICY_BOTTOM_PROBABLE` | 三重共振，政策底信号（推断，非实锤） |
| `INFLOW_ONLY` | 仅观测到增量资金承接，**不标记为国家队进场** |
| `ANNOUNCEMENT_ONLY` | 仅有公告，无资金面印证 |
| `NO_SIGNAL` | 无托底信号 |

---

## 三、不变式（由测试守护）

1. `is_confirmed` 在所有状态、所有历史记录中恒为 `False`；
2. 基于 L2 的推断 `confidence != "high"`；
3. 证金系列别名全部归一到 `csfc`，核心主体中只计一次；
4. `effective_date == disclose_date >= report_period_end`；
5. 名单新进/退出与被动变动均 `is_actionable=False`；
6. 相同随机种子生成的演示数据完全一致；
7. 标的池恰为 7 只、代码唯一、`159845` 管理人必须为华夏；
8. 量能 `summary.is_signal` 恒为 `False`，且输出文案不含买/卖/建仓等指令措辞；
9. `signals.conditions` 集合恒为三重共振三条件，量能观测不得成为第四条件。
10. 演示数据集不得通过 `validate_dataset()`；真实适配器失败不得降级回演示数据；
    `GET /api/datasource` 不得返回任何凭据值。

---

## 四、`GET /api/datasource` 契约

| 字段 | 类型 | 说明 |
|------|------|------|
| `mode` | str | `demo` / `snapshot` / `live` |
| `mode_label` | str | 中文模式名 |
| `is_demo` | bool | 当前数据集是否为合成演示数据 |
| `verified` | bool | 真实数据源是否完成核验清单 |
| `dataset_source` / `dataset_as_of` | str | 数据集来源与基准日 |
| `credential_configured` / `endpoint_configured` | bool | **仅布尔值，绝不返回凭据内容** |
| `ready_to_switch` | bool | 端点、凭据、核验三项是否齐备 |
| `layers[]` | list | 三层接入状态、必需字段、是否可归因 |
| `gaps[]` | list | 当前接入缺口与口径约束 |
| `iron_rules[]` | list | 接入真实数据后依然成立的铁律 |
| `wind_alice_review` | dict | 2026-11-10 对 Wind Alice Market 推文的核查结论 |

真实数据源请求契约（`src/ntm/adapters/live.py`）：
`POST {NTM_DATA_ENDPOINT}`，`Authorization: Bearer <NTM_DATA_API_KEY>`，
响应为 `{"dataset": {market, etf, announcements, holdings}}` 或直接返回 dataset。
`etf` 每条必须含 `total_share / nav / share_change / est_net_yi / source`，
且 `source` 不得含 `demo / synthetic / mock / 演示 / 模拟` 标记。
校验不通过时抛 `LiveDataError`，**不入库、不降级**。
