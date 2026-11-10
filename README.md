# 国家队资金三层监控看板（National Team Monitor）

> **先读这句**：公开数据**无法日频归因到国家队**。本系统不是「跟单系统」，
> 不提供个股买卖建议。它做的是：把国家队相关公开信息分成
> **L1 实测 / L2 估算 / L3 代理**三层，输出**带置信度、明确标注「推断非实锤」**的
> 托底信号，供人工判断「是否进场、仓位怎么摆」。

本项目由《A股国家队资金监控与择时平台 · 开发提示词整理文档》经
[完备性与精准性研判](../A股国家队资金监控平台_开发提示词_完备性与精准性研判.md)
修订后落地实现。修订版提示词全文见 [`docs/REVISED_PROMPT.md`](docs/REVISED_PROMPT.md)，
方法论见 [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md)。

---

## 快速开始（3 条命令）

```bash
cd national-team-monitor
pip install -r requirements.txt
PYTHONPATH=src python3 -m ntm serve
```

打开 <http://localhost:8000>。首次启动自动建库并装载**演示数据**（页面顶部有醒目警示）。

验证是否真的跑通：

```bash
PYTHONPATH=src python3 -m ntm check     # 自检：实体去重/PIT/信号铁律
python3 -m pytest tests/ -q             # 37 项测试
curl http://localhost:8000/api/health
```

---

## 它解决什么问题

| 你的诉求 | 本系统 |
|---|---|
| 每天知道国家队买了哪只股、照着买 | ❌ 做不到，系统明确不提供 |
| 每天判断「是不是在托底、该不该进场」 | ✅ 三重共振 + 置信度 + 局限说明 |
| 季度复盘国家队持仓方向 | ✅ L1 确权数据，带 PIT 时点 |

**三重共振判定**：市场大跌 ① + 多只核心宽基 ETF 份额同步跳升 ② + 官方增持类公告 ③。
三者齐备才记为「政策底信号（推断）」；缺任一条件只记「有大额增量资金承接」，
**绝不标记为国家队进场**。所有信号 `is_confirmed=false`。

### 7 只宽基 ETF 标的池与量能观察

系统内置 7 只核心宽基 ETF 观察池（`510050 / 510300 / 510500 / 512100 /
159845 / 588000 / 159915`），登记跟踪指数、板块方向与管理人，并计算成交额环比、
放量倍数（当日 ÷ 前 5 日均额）。

> **关键口径**：成交额放量是二级市场交易活跃度，**不改变 ETF 总份额、无法识别
> 买方身份**。因此量能观测是独立辅助层，`is_signal` 恒为 `false`，
> **不进入三重共振判定**；「放量 2 倍」不等于份额增加，更不等于国家队加仓。
> 看板把成交额与份额变化分列对照，演示数据还内置了一个「全部放量、份额近零变化」
> 的反例日用于直观验证。

`159845` 的材料名称「易方达中证1000ETF」已核实修正为**华夏中证1000ETF**
（易方达那只代码为 `159633`）；「持仓占比 20–40%」「2026 布局转向科创50/创业板」
属未确权观点，仅作前端高亮，不参与计算。详见 `docs/REFERENCE_STRATEGY.md`。

### 每日复盘（约 10 分钟）

固定顺序：**量能观测 → 份额（L2 估算）→ 公告（L3）→ 三重共振信号**。
量能只是入口不是结论；系统不输出买卖信号、仓位或择时指令。
用户提供的三套监控方案、交易执行规则、复盘流程、风险提示全部原文存档并逐条标注
处置结论（已实现 / 保留为参考 / 不实现），见 `docs/REFERENCE_STRATEGY.md`
与 `GET /api/reference/strategy`。

---

## 技术栈与取舍

| 项 | 选择 | 理由 |
|---|---|---|
| 后端 | Python 3.11 + FastAPI | 原文档技术偏好 |
| 存储 | SQLite（标准库 `sqlite3`） | 原版 PostgreSQL/TimescaleDB 与 SQLite 冲突；MVP 单机部署取 SQLite，降低部署复杂度。数据量上来后迁移路径写在 `docs/METHODOLOGY.md` |
| 调度 | 标准库线程 | 避免引入 APScheduler/Celery 等中间件 |
| 前端 | 单页 HTML + ECharts CDN | 原版 React/Vue 需构建链；单容器可直接 serve |
| 指标计算 | 标准库 | 逻辑简单，无需 Pandas；保留可扩展性 |

原文档中「实时/准实时跟踪交易行为」「个股层面国家队加仓机会筛选」「依赖主力资金流入」
等不成立或误导的承诺已删除，逐条对照见 `docs/REVISED_PROMPT.md`。

---

## 命令

```bash
python -m ntm init                 # 初始化数据库
python -m ntm refresh              # 刷新数据 + 重算信号 + 预警
python -m ntm refresh --as-of 2026-11-09
python -m ntm serve                # 启动 Web 服务
python -m ntm backtest --horizon 20
python -m ntm check                # 自检（实体/PIT/信号不变式）
```

配置全部走环境变量，模板见 [`.env.example`](.env.example)。

---

## 目录结构

```
national-team-monitor/
├── src/ntm/
│   ├── config.py           # 配置（凭据只读环境变量）
│   ├── entities.py         # 主体清单 + 别名归一化（修正证金重复、排除中投）
│   ├── etf_pool.py         # 7 只宽基 ETF 标的池、159845 修正、未确权观点隔离
│   ├── momentum.py         # 环比/放量倍数/量能汇总（纯函数，恒非信号）
│   ├── reference.py        # 参考策略原文存档与逐条实现取舍
│   ├── layers.py           # 三层模型、术语表、四个陷阱、噪音清单
│   ├── cleaning.py         # 名单进出≠增减持 / 复权 / PIT 防前视
│   ├── signals.py          # 三重共振状态机 + 未确权铁律
│   ├── db.py               # SQLite 存储与查询（含旧库迁移、量能查询）
│   ├── backtest.py         # 防前视回测 + 基准对照
│   ├── alert.py            # 企业微信/邮件/站内预警（去重降噪、频率上限）
│   ├── scheduler.py        # 每交易日盘后刷新
│   ├── api.py              # FastAPI 接口 + 静态页
│   ├── adapters/           # demo（默认）/ live（骨架，默认禁用）
│   └── static/index.html   # 单页看板
├── tests/                  # 59 项测试
├── docs/                   # 修订版提示词 / 方法论 / 数据字典 / 参考策略存档
├── deploy/                 # 部署脚本、Nginx、systemd、crontab
├── scripts/                # GitHub 推送（git 与 API 双路径）
├── Dockerfile / docker-compose.yml
└── .github/workflows/ci.yml
```

---

## 测试

```bash
python3 -m pytest tests/ -q
```

覆盖：证金别名收敛、中投排除、名单进出不可判定、送转股复权、PIT 前视检测、
三重共振四种状态、`is_confirmed` 恒假、置信度上限、演示数据可复现、API 契约、
刷新链路、回测 PIT 校验；以及标的池 7 只与 `159845` 修正、交易所前缀匹配、
未确权观点隔离、放量倍数/环比纯函数、量能观测恒非信号、文案无交易指令措辞、
量能不污染三重共振条件集合。

```bash
python3 -m ntm check     # 自检：含标的池与量能中性约束
```

### 主要接口

除原有 `/api/overview`、`/api/etf/shares`、`/api/signals` 等外，本版新增：

| 接口 | 返回 |
|---|---|
| `GET /api/etf/pool` | 7 只标的池、未确权参考观点、校验结果 |
| `GET /api/etf/momentum?day=YYYY-MM-DD` | 成交额/环比/放量倍数/份额对照、量能汇总（`is_signal=false`） |
| `GET /api/reference/strategy` | 参考策略原文与逐条实现取舍 |

完整契约见 `docs/DATA_DICTIONARY.md`。

---

## 部署

### 方式 A：本地 / 单机直接跑
见「快速开始」。

### 方式 B：Docker（推荐）

```bash
cp .env.example .env
docker compose up -d --build
curl http://localhost:8000/api/health
```

数据卷 `ntm-data` 持久化 SQLite。镜像以非 root 用户运行，含 HEALTHCHECK。

### 方式 C：一键部署到你的云服务器

```bash
export SERVER_HOST="你的服务器IP或域名"
export SERVER_USER="root"           # 或具 sudo 权限的账号
export SSH_KEY="$HOME/.ssh/id_rsa"  # 可选，默认走 ssh-agent / 默认密钥
export DOMAIN=""                    # 可选，配置了才启用 Nginx 反代
./deploy/deploy.sh
```

脚本会：本地打包 → `scp` 上传 → 远端解压到 `/opt/national-team-monitor` →
`docker compose up -d --build` → 轮询 `/api/health` → 输出**真实**访问地址。
失败时保留上一版本并给出回滚命令，不会静默声称成功。

细节与前置条件见 [`deploy/README.md`](deploy/README.md)。也可用
`deploy/ntm.service`（systemd）或 `deploy/crontab.example`（不用容器）。

> ⚠️ 本仓库**未包含任何真实云主机凭据**，脚本需要你在自己的环境提供
> `SERVER_HOST` / `SERVER_USER` / SSH 私钥。

---

## 推送 GitHub

**方式 A（推荐）**：在 CodeBuddy 设置页 →「连接器」→ 授权 GitHub，然后直接：

```bash
./scripts/push_github.sh
```

> **连接器令牌的已知限制**：GitHub 连接器返回的是 GitHub App 用户令牌，
> 实测 `POST /user/repos`（新建个人仓库）返回 403
> `Resource not accessible by integration`，所需权限为
> `administration=write; repository_creation=write`，该类令牌默认不具备。
> 但它对**已存在的仓库**有完整写权限（实测 `push=True`）。因此使用连接器时需先在
> GitHub 网页创建一个**空仓库**（不勾选 README / .gitignore / License），
> 脚本会自动识别「仓库已存在」并跳过创建、直接推送：
>
> `https://github.com/new?name=national-team-monitor&visibility=public`
>
> 若改用具备 `repo` 权限的 classic PAT，则无需预建仓库，脚本可全自动建仓并推送。

**方式 B**：自带 Personal Access Token（需 `repo` 权限；fine-grained 需
Contents 读写 + Metadata 只读）。推荐用**凭据文件**，令牌不会出现在命令文本里：

```bash
# token.txt 仅一行令牌，权限建议 600，用完即删
GITHUB_TOKEN_FILE=/path/to/token.txt ./scripts/push_github.sh

# 或直接走环境变量（令牌会出现在命令文本中）
GITHUB_TOKEN=ghp_xxx ./scripts/push_github.sh
```

可选环境变量：`GITHUB_REPO`（默认 `national-team-monitor`）、`GITHUB_OWNER`
（默认取 token 所属用户）、`GITHUB_PRIVATE=1`（创建私有仓库）、
`GITHUB_PATH_PREFIX`（仓库内子目录）、`GITHUB_BRANCH`（默认 `main`）。
凭据优先级：`GITHUB_TOKEN` → `GITHUB_TOKEN_FILE` → GitHub 连接器 OAuth。

脚本执行 5 步：本地提交 → 取凭据 → 身份确认 → 推送 → 输出地址。推送路径自动选择：

1. **REST API 单提交**（优先）：`api.github.com` 认证通过时，用
   `scripts/push_via_api.py` 经 Git Data API 自动创建/复用仓库并一次提交全部文件；
   不依赖 git 协议、`gh` 登录或预建仓库。
2. **git 协议子目录推送**（API 写权限不足时自动降级）：部分令牌（如 GitHub App
   用户到服务器令牌 `ghu_`）无 `contents=write` 权限，Git Data API / Contents API
   均返回 403。此时改用 `scripts/push_via_git.py`：浅克隆 → 复制文件到子目录 →
   校验原有文件字节级零改动 → 单次提交 → 带重试推送。git 推送前使用
   `oauth2:<token>` 的 Basic 认证与 HTTP/1.1（实测 `x-access-token` 格式与 HTTP/2 均超时）。
3. **复用已有仓库**：`GITHUB_REPO=lpp GITHUB_PATH_PREFIX=national-team-monitor
   ./scripts/push_github.sh` 会把项目写入 `lpp` 的 `national-team-monitor/` 子目录，
   仓库根下原有文件保持不动；同名路径冲突时原内容自动备份到 `_backup_before_push/`。

> **写权限的硬约束**：GitHub 连接器返回的是 `ghu_` 用户到服务器令牌，`contents`
> 权限由 App 注册时固定，用户授权时无法提升。实测 Git Data API、Contents API、
> `git push` 三条写通道均要求 `contents=write`。因此完成推送需要：
> **具备 `repo` 权限的 classic PAT（`ghp_`）**，或由 App 方开放写权限的连接器。
> 仅有读权限时脚本会明确报错退出，不会静默成功或虚构线上地址。

凭据只经环境变量与命令替换传递，**不打印、不写入 `.git/config`、不进版本库**。
未授权时脚本以退出码 2 明确停止并打印诊断，不会静默成功或虚构线上地址。

---

## 数据源与合规

- 默认演示数据为**合成数据**（固定种子 20261109），页面与 API 强制标注 `is_demo`；
- 真实数据源适配器默认禁用，接入前必须完成
  [`docs/METHODOLOGY.md` 第八节](docs/METHODOLOGY.md) 的核验清单（授权、版权、频控、
  字段含义、披露日抓取、交易日历等）；
- **明确排除**行情软件「主力资金流入 / 国家队资金流向」指标、龙虎榜所谓国家队席位、
  网传「今日买入清单」，以及「多只 ETF 放量 2 倍 = 国家队加仓/减仓」这类组合误读；
- 即使接入真实数据，ETF 份额依然**无法识别申购人**，`is_confirmed` 永远为 `false`；
- 成交额放量只作辅助观测，接入真实成交额后也不升级为信号。

---

## 已知限制

| 限制 | 说明 |
|---|---|
| 演示数据 | 不可用于实盘决策 |
| 交易日历 | 仅判周末，节假日需接入交易所日历 |
| SQLite | 单实例写，多实例需迁 PostgreSQL/TimescaleDB |
| 回测 | 未做撮合级模拟（滑点/涨跌停/停牌） |
| 样本量 | 国家队进场事件历史上为个位数，统计结论极易过拟合 |
| 成交额 | 演示合成；放量倍数仅供观测，不升级为信号 |
| 未确权观点 | 「持仓占比 20–40%」「2026 布局转向」无日频公开口径，只作参考 |
| 商业数据 | 万得/同花顺申赎明细与推送未接入，需账号授权与合规核验 |
| CORS | 默认 `*`，生产请收敛到具体域名 |

---

## 免责声明

> ⚠️ 本系统所有「国家队资金」判断均为基于公开数据的**概率推断，无法确权**；
> L2 仅代表全市场一级市场净申购、无法识别申购人。系统**不提供任何个股买卖建议**，
> 不构成投资建议或择时建议。政策底不等于市场底（历史间隔 40 天至 6 个月）。
> 市场有风险，投资需谨慎，决策与风险由使用者自行承担。

License: MIT
