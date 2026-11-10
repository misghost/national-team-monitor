# 个人使用指南（Wind / iFinD）

本仓库已加入个人节奏观察能力。目标是把核心宽基 ETF 的一级市场份额变化
（最强公开代理）与指数跌幅结合，帮你更快感知「是否有政策托底节奏」。

**重要**：即使接入 Wind / iFinD，仍无法日频确权到国家队具体账户。
所有输出仅供个人观察参考，不构成投资建议。

日常命令与适配器选择见下文。Wind MCP 的既有接入方式不变，见 README「真实数据接入」。

## 环境变量（凭据只走环境变量）

账号、密码、refresh token、API Key 只放在进程环境里。不要写进代码、不要提交到仓库、
不要打进日志。下面示例里的值都是占位符。

### 演示（无需 Wind / iFinD）

```bash
export NTM_DEMO_MODE=1
PYTHONPATH=src python3 scripts/daily_personal.py
```

`NTM_DEMO_MODE=1`（也是默认值）时工厂始终使用演示数据，忽略 `NTM_DATA_SOURCE`。

### Wind（推荐，本机已登录终端）

```bash
export NTM_DEMO_MODE=0
export NTM_DATA_SOURCE=windpy
export NTM_DATA_VERIFIED=1
# 可选覆盖份额字段（用超级命令确认）
# export NTM_WIND_SHARE_FIELD=f_unit_floortrading
```

不需要 `NTM_ENABLE_LIVE_ADAPTERS`。该开关仍只约束 Wind MCP 与通用 HTTP 适配器。
Wind 终端自己的登录态由客户端保管，本程序不读取 Wind 账号密码。

### iFinD

```bash
export NTM_DEMO_MODE=0
export NTM_DATA_SOURCE=ifind
export NTM_DATA_VERIFIED=1
export NTM_IFIND_USER=你的账号
export NTM_IFIND_PASS=你的密码
# 或使用 refresh_token 方式（HTTP 解析尚未完成，目前会明确失败而不是假装取到数）
# export NTM_IFIND_REFRESH_TOKEN=xxx
# 可选覆盖份额字段
# export NTM_IFIND_SHARE_FIELD=ths_unit_floortrading_fund
```

同时提供了账号密码和 refresh token 时，优先走 iFinDPy。

## 日常使用

```bash
cd national-team-monitor
PYTHONPATH=src python3 scripts/daily_personal.py
```

输出包含：
- 关注强度分数（0-85）
- 近3日 / 近5日核心宽基合计净申购
- 依据说明
- 近7日明细

## 首次使用建议

1. 在 Wind / iFinD 终端用「超级命令」确认「场内流通份额」对应的确切字段名。
2. 用环境变量 `NTM_WIND_SHARE_FIELD` 或 `NTM_IFIND_SHARE_FIELD` 覆盖默认值。
3. 先跑一次 `python3 -m ntm refresh`，检查 `data/` 下是否生成历史文件。
4. 再跑 `scripts/daily_personal.py` 看日报。

WindPy 历史快照写在 `data/windpy_history.json`（可用 `NTM_DATA_DIR` 改目录）。
这里只存份额与行情，不写凭据。`data/` 已被 `.gitignore` 排除。

## 已知限制

- 份额字段默认名（Wind `f_unit_floortrading`、iFinD `ths_unit_floortrading_fund`）
  没有在本仓库里用真实终端核对过，必须以你本机超级命令的结果为准。
- iFinD 适配器目前是可运行骨架：SDK 能登录，但返回格式因版本而异，解析未完成时会
  报错停止，**不会降级成演示数据**。见 `src/ntm/adapters/ifind.py`。
- refresh_token 的 HTTP 通道同样会明确失败，直到按实际响应补上解析。
- 公告仍建议人工维护 `data/verified_announcements.json`（避免误用新闻标题）。
  WindPy 刷新会读取这份文件；系统不会根据标题自动判定 `is_supportive`。
- 分数与阈值可在 `src/ntm/personal.py` 中自行调整。
- 真实数据未通过 `validate_dataset` 时整次采集丢弃，不入库。

数据字段以你终端实际可用为准。
