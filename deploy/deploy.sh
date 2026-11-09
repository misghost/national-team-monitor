#!/usr/bin/env bash
# 一键部署到用户自己的云服务器。
# 必需：SERVER_HOST、SERVER_USER；可选：SSH_KEY、DOMAIN、REMOTE_PORT、REMOTE_DIR。
# 设计原则：每一步都实际校验，健康检查通过后才输出访问地址；
#           失败时打印日志并明确报错，绝不静默声称部署成功。
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_DIR="${REMOTE_DIR:-/opt/national-team-monitor}"
REMOTE_PORT="${REMOTE_PORT:-8000}"
SERVER_HOST="${SERVER_HOST:-}"
SERVER_USER="${SERVER_USER:-root}"
SSH_KEY="${SSH_KEY:-}"
DOMAIN="${DOMAIN:-}"
SSH_PORT="${SSH_PORT:-22}"

for var in SERVER_HOST SERVER_USER; do
  if [[ -z "${!var}" ]]; then
    echo "[ERROR] 缺少必需环境变量 ${var}" >&2
    echo "示例：SERVER_HOST=1.2.3.4 SERVER_USER=root ./deploy/deploy.sh" >&2
    exit 2
  fi
done

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -p "$SSH_PORT")
SCP_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -P "$SSH_PORT")
[[ -n "$SSH_KEY" ]] && SSH_OPTS+=(-i "$SSH_KEY") && SCP_OPTS+=(-i "$SSH_KEY")
TARGET="${SERVER_USER}@${SERVER_HOST}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
TARBALL="$TMP/ntm.tar.gz"

echo "[1/6] 打包项目（排除数据、凭据与构建产物）"
tar -czf "$TARBALL" -C "$ROOT" \
  --exclude='./.git' \
  --exclude='./data' \
  --exclude='./.env' \
  --exclude='./.venv' \
  --exclude='./venv' \
  --exclude='./__pycache__' \
  --exclude='./.pytest_cache' \
  --exclude='*.pyc' \
  --exclude='*.db' \
  --exclude='*.db-shm' \
  --exclude='*.db-wal' \
  --exclude='*.log' \
  --exclude='*.tar.gz' \
  .
echo "      包大小：$(du -h "$TARBALL" | cut -f1)"

echo "[2/6] 上传到 ${TARGET}"
scp "${SCP_OPTS[@]}" "$TARBALL" "${TARGET}:/tmp/ntm.tar.gz"
if [[ -f "$ROOT/.env" ]]; then
  scp "${SCP_OPTS[@]}" "$ROOT/.env" "${TARGET}:/tmp/ntm.env"
  echo "      已上传 .env（含配置，不含日志）"
fi

echo "[3/6] 远端解压与配置"
ssh "${SSH_OPTS[@]}" "$TARGET" bash -s <<REMOTE
set -Eeuo pipefail
mkdir -p "${REMOTE_DIR}"
tar -xzf /tmp/ntm.tar.gz -C "${REMOTE_DIR}"
if [[ -f /tmp/ntm.env ]]; then
  mv /tmp/ntm.env "${REMOTE_DIR}/.env"
elif [[ ! -f "${REMOTE_DIR}/.env" ]]; then
  cp "${REMOTE_DIR}/.env.example" "${REMOTE_DIR}/.env"
  echo "      远端 .env 由模板生成，请按需填写预警凭据"
fi
mkdir -p "${REMOTE_DIR}/data"
rm -f /tmp/ntm.tar.gz
command -v docker >/dev/null || { echo "[ERROR] 远端未安装 docker" >&2; exit 3; }
docker compose version >/dev/null 2>&1 || { echo "[ERROR] 远端缺少 docker compose 插件" >&2; exit 3; }
REMOTE

echo "[4/6] 构建并启动容器"
if [[ -n "$DOMAIN" ]]; then
  ssh "${SSH_OPTS[@]}" "$TARGET" "cd ${REMOTE_DIR} && docker compose --profile nginx up -d --build"
else
  ssh "${SSH_OPTS[@]}" "$TARGET" "cd ${REMOTE_DIR} && docker compose up -d --build"
fi

echo "[5/6] 健康检查（最多等待 60 秒）"
ok=0
for _ in $(seq 1 30); do
  if ssh "${SSH_OPTS[@]}" "$TARGET" \
      "curl -fsS --max-time 4 http://127.0.0.1:${REMOTE_PORT}/api/health >/dev/null 2>&1 || wget -qO- --timeout=4 http://127.0.0.1:${REMOTE_PORT}/api/health >/dev/null 2>&1"; then
    ok=1
    break
  fi
  sleep 2
done
if [[ "$ok" -ne 1 ]]; then
  echo "[FAILED] 健康检查未通过，部署未确认成功。容器日志：" >&2
  ssh "${SSH_OPTS[@]}" "$TARGET" "cd ${REMOTE_DIR} && docker compose logs --tail=80" >&2 || true
  echo "回滚：ssh ${TARGET} 'cd ${REMOTE_DIR} && docker compose down'" >&2
  exit 1
fi

echo "[6/6] 部署完成，健康检查通过"
echo "      /api/health : $(ssh "${SSH_OPTS[@]}" "$TARGET" "curl -fsS http://127.0.0.1:${REMOTE_PORT}/api/health")"
if [[ -n "$DOMAIN" ]]; then
  echo "      访问地址   : http://${DOMAIN}  (Nginx 反代，80 端口)"
else
  echo "      访问地址   : http://${SERVER_HOST}:${REMOTE_PORT}"
  echo "      提示：云服务器安全组需放行 TCP ${REMOTE_PORT}；生产建议配置 DOMAIN + TLS"
fi
echo "      查看日志   : ssh ${TARGET} 'cd ${REMOTE_DIR} && docker compose logs -f'"
