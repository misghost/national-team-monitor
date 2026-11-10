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

不需要 `NTM_ENABLE_LIVE_ADAPTERS`。该开关只约束未指定来源的通用 HTTP 适配器。
Wind 终端自己的登录态由客户端保管，本程序不读取 Wind 账号密码。

### Wind MCP（Linux 服务器，不需要本机 Wind 终端）

托管地址是 `https://mcp.wind.com.cn/vserver_wind_tools/mcp/`。密钥只放 `NTM_WIND_MCP_KEY`，不要放进仓库。

```bash
export NTM_DEMO_MODE=0
export NTM_DATA_SOURCE=wind_mcp
export NTM_DATA_VERIFIED=1
export NTM_WIND_MCP_KEY=你的Wind密钥
PYTHONPATH=src python3 scripts/daily_personal.py
```

份额用已核对的 `fund_get_size`（场内流通份额，单位份）。指数日序列选用搜索结果里的 `securities_get_trend`；它的参数表在现有样本里不完整，程序只在这时再调 `market_search_tools`。

### iFinD

```bash
export NTM_DEMO_MODE=0
export NTM_DATA_SOURCE=ifind
export NTM_DATA_VERIFIED=1
export NTM_IFIND_USER=你的账号
export NTM_IFIND_PASS=你的密码
```

有 refresh token 时走 HTTP，不再要求本机安装 iFinDPy。见下一节。

## Linux 服务器：iFinD HTTP

不需要 iFinDPy。令牌只放环境变量，进程里换到的 access_token 只留在内存。

```bash
export NTM_DEMO_MODE=0
export NTM_DATA_SOURCE=ifind
export NTM_DATA_VERIFIED=1
export NTM_IFIND_REFRESH_TOKEN=你的refresh_token
# 也认 ifind_api_refresh_token。已有 access_token 时可另设 ifind_api_access_token，
# 失效时仍用 refresh_token 重换。
#
# 技能包 v1.1.0 没有收录「场内流通份额」和 IOPV 的指标代码。
# 指数收盘价用文档里的 close。份额和净值必须你自己确认后填入，否则程序会失败停止：
export NTM_IFIND_SHARE_FIELD=确认后的份额指标
export NTM_IFIND_SHARE_PARAMS='[]'        # get_indicator_params 的顺序；没有参数就填 []
export NTM_IFIND_NAV_FIELD=确认后的净值指标
export NTM_IFIND_NAV_PARAMS='[]'
PYTHONPATH=src python3 scripts/daily_personal.py
```

免费档按 10 次/秒/函数、合计 20 次/秒 节流。单次单元格上限默认 2000（技能包没写死这个数字，可用 `NTM_IFIND_MAX_CELLS` 改）。

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

- Wind 场内流通份额字段 `f_unit_floortrading` 已用 2026-11-10 的 510300.SH / 510050.SH 样本核对，单位是份。
- iFinD 份额和净值指标代码**没有**出现在技能包 v1.1.0 里。不设置 `NTM_IFIND_SHARE_FIELD` / `NTM_IFIND_NAV_FIELD` 时会失败，不会猜一个字段名再写入空数据。
- iFinDPy 的 SDK 返回格式仍未核对。服务器请用上面的 HTTP 通道。
- 公告仍建议人工维护 `data/verified_announcements.json`（避免误用新闻标题）。
  WindPy 刷新会读取这份文件；系统不会根据标题自动判定 `is_supportive`。
- 分数与阈值可在 `src/ntm/personal.py` 中自行调整。
- 真实数据未通过 `validate_dataset` 时整次采集丢弃，不入库。

数据字段以你终端实际可用为准。
