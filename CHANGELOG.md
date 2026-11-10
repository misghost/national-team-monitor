# 变更记录

本项目遵循语义化版本。所有条目按时间倒序。

## [Unreleased]

### 新增

- 个人节奏观察：`personal.py`、`scripts/daily_personal.py`、[`PERSONAL_USAGE.md`](PERSONAL_USAGE.md)。
  分数综合指数跌幅、核心宽基净申购与人工核验公告，上限 85，不输出买卖指令。
- 适配器 `windpy`（本机 Wind 终端）与 `ifind`（同花顺 iFinD）。
  `NTM_DATA_SOURCE=windpy|ifind` 即可选择；WindPy / iFinDPy / 企业微信用的 `requests` 改为用到时再导入。

### 适配

- `get_etf_series` 给出的是按日汇总字段 `net_subscription_yi`，个人分数按该字段取值，避免净申购被读成 0。
- `get_signals` 不含 `state_label`，日报输出前用既有状态枚举补上中文标签。
- WindPy 读取人工维护的 `verified_announcements.json`；校验失败丢弃本次采集，不入库、不降级。
- `wind_mcp` 与通用 HTTP 适配器仍必须设置 `NTM_ENABLE_LIVE_ADAPTERS`。个人终端适配器不使用这个开关。
- iFinD HTTP：用 refresh_token 换 access_token（只缓存在内存），按官方技能包调用 history_data / date_sequence / get_trade_dates。份额与净值指标代码文档未收录，未配置时失败停止。
- Wind MCP 改为托管端点，密钥只读 `NTM_WIND_MCP_KEY`。份额字段改为已核对的 `f_unit_floortrading` 等；指数序列改用 `securities_get_trend`，参数定义不全时才搜索工具。

## [1.0.0] — 2026-11-10

首个完整发布版本。

### 方法论与边界

- 确立 **L1 实测 / L2 估算 / L3 代理** 三层模型，明确从「跟单系统」改为
  「分层监控 + 带置信度的概率推断」；
- 实现**三重共振**状态机（4 种状态、置信度上限 `medium`），
  所有信号 `is_confirmed=False`；
- 实体别名归一化（证金系列收敛为 `csfc`）、排除中投等非 A 股主体、
  PIT 防前视披露日、送转股复权四条硬性清洗规则；
- 明确排除「主力资金流入」「龙虎榜国家队席位」「放量 2 倍 = 加仓」等误读口径。

### 数据能力

- 7 只宽基 ETF 结构化标的池，修正 `159845` 为**华夏中证1000ETF**；
- 量能观察层（成交额环比、前 5 日均额放量倍数），`is_signal=False`，
  不进入三重共振条件集合；
- 参考策略原文存档 + 17 条逐项处置（已实现 8 / 保留参考 3 / 不实现（合规）6）；
- 新增 `datasource.py` 校验闸门：拒绝以成交额顶替 `total_share`、
  demo 来源标记、未来日期 / PIT 前视、`attributable=True`；
- 新增 `WindMCPAdapter`：对接 `get_index_kline` / `fund_get_size` /
  `fund_get_listed_historical_price`，覆盖 L2 **ETF 日频总份额**缺口；
- 修复 SSE 含中文时被 `splitlines()` 误切 JSON 的缺陷（改为 UTF-8 解码 + 仅按 `\n` 切分）；
- 处理 **T+1 份额时滞**：当日份额未发布时跳过该日、各层统一截到最新份额日，
  不拿旧数据冒充当日；
- 新增 `data/wind_history.json` 每日快照累积，规避 `save_dataset` 全量替换清空历史。

### 接口与展示

- 14 个 API 接口，新增 `/api/etf/pool`、`/api/etf/momentum`、
  `/api/reference/strategy`、`/api/datasource`；
- 看板新增标的池表格、量能异动观察、参考策略存档、数据源接入状态四个区块；
- 演示数据 `verified` 语义修正（不再把「合成数据自洽」误标为「数据源已核验」）；
- L3 官方公告改为**人工确权通道**（`verified_announcements.json`），不自动判定。

### 工程与部署

- 95 项 pytest + `ntm check` 9 组不变式自检；
- Dockerfile / docker-compose / systemd / Nginx / cron 定时刷新 / CI 双作业；
- GitHub 推送（REST API 单提交 + git 协议降级）、GitCode / Gitee 推送脚本；
- 完整文档体系：`README.md`、`docs/COMPLETE_GUIDE.md`、`METHODOLOGY.md`、
  `DATA_DICTIONARY.md`、`REFERENCE_STRATEGY.md`、`REVISED_PROMPT.md`、
  `SECURITY.md`、本文件。

### 说明

- 默认仍为**合成演示数据**，`is_demo=true`，不可用于实盘决策；
- 即使接入真实数据，L2 份额依然无法识别申购人，`is_confirmed` 恒为 `False`；
- L1 季度十大流通股东、L3 自动公告确权仍为缺口，系统显式标注、不伪造。
