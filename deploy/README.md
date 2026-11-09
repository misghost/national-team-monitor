# 部署说明

## 前置条件（在**你的**云服务器上）

| 项 | 要求 |
|---|---|
| 操作系统 | Linux（Ubuntu 22.04 / Debian 12 / CentOS Stream 均可） |
| Docker | ≥ 24，且安装 `docker compose` 插件 |
| 端口 | 放行 TCP 8000（直连模式）或 TCP 80（Nginx 模式） |
| SSH | 本地可免密或密钥登录目标机 |
| 内存 | ≥ 512MB（演示数据规模很小） |

> 本仓库**不包含、也不需要**任何云主机凭据。凭据只在执行时由环境变量提供。

## 一键部署

```bash
export SERVER_HOST="1.2.3.4"
export SERVER_USER="root"
export SSH_KEY="$HOME/.ssh/id_rsa"     # 可选
export DOMAIN=""                        # 可选；非空则启用 Nginx 反代（80 端口）
./deploy/deploy.sh
```

脚本阶段：打包 → scp 上传 → 远端解压与 `.env` 准备 → `docker compose up -d --build`
→ 轮询 `/api/health` → **仅在健康检查通过后**输出真实访问地址。

**失败处理**：健康检查不通过时脚本打印容器日志、明确退出码 1，
并给出回滚命令，不会声称部署成功。

## 手动回滚

```bash
ssh "$SERVER_USER@$SERVER_HOST"
cd /opt/national-team-monitor
docker compose logs --tail=200        # 先定位
docker compose down                   # 停止
# 恢复上一版代码后：
docker compose up -d --build
```

数据卷 `ntm-data` 独立于容器生命周期，`docker compose down` 不会删除数据。
**不要**用 `docker compose down -v`，那会删除卷。

## 生产加固清单

- [ ] 配置 `DOMAIN` + TLS（本仓库 `deploy/nginx.conf` 仅提供 HTTP 反代，未含证书）
- [ ] 把 `api.py` 的 CORS `allow_origins=["*"]` 收敛到具体域名
- [ ] `.env` 设置 `NTM_DEMO_MODE=0` 前，必须完成
      [`docs/METHODOLOGY.md`](../docs/METHODOLOGY.md) 第八节的真实数据源核验
- [ ] 配置 `NTM_WECHAT_WEBHOOK` / `NTM_SMTP_*` 启用预警（留空即跳过）
- [ ] 用 Nginx/网关加访问控制，避免看板裸露公网
- [ ] 定期备份 `ntm-data` 卷

## 不用 Docker 的两种方式

**systemd**（适合已有 Python 环境）：

```bash
sudo useradd --system ntm
sudo mkdir -p /opt/national-team-monitor
# 拷贝项目后
python3 -m venv /opt/national-team-monitor/.venv
/opt/national-team-monitor/.venv/bin/pip install -r requirements.txt
sudo cp deploy/ntm.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ntm
```

**crontab**（只跑定时刷新、不常驻服务）：见 `deploy/crontab.example`。

## 已知边界

- 默认演示数据是合成数据，**不可用于实盘决策**；
- 交易日历仅判周末，节假日需接入交易所日历；
- SQLite 单实例写，多实例需迁 PostgreSQL/TimescaleDB。
