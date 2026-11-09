#!/usr/bin/env bash
# 推送到 GitHub。自动选择可达路径：
#   路径 A：github.com 可达  → git push（gh repo create --push）
#   路径 B：github.com 不可达但 api.github.com 可达 → REST API 单提交推送
# 无论走哪条路径，都会先完成本地 git 初始化与规范提交。
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REPO="${GITHUB_REPO:-national-team-monitor}"
OWNER="${GITHUB_OWNER:-}"
TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"

echo "[1/4] 本地 git 初始化与提交"
if [[ ! -d .git ]]; then
  git init -q
  git branch -M main
fi
# 不覆盖用户已有身份配置
if [[ -z "$(git config user.name 2>/dev/null || true)" ]]; then
  git config user.name "NTM Bot"
fi
if [[ -z "$(git config user.email 2>/dev/null || true)" ]]; then
  git config user.email "ntm-bot@localhost"
fi
git add -A
if git diff --cached --quiet; then
  echo "      无新增改动，复用现有提交"
else
  git commit -q -m "feat: 国家队资金三层监控看板（修订版提示词落地实现）"
  echo "      已创建本地提交：$(git rev-parse --short HEAD)"
fi

echo "[2/4] 探测网络路径"
GIT_OK=0
API_OK=0
if curl -s --max-time 8 -o /dev/null https://github.com; then GIT_OK=1; fi
if curl -s --max-time 8 -o /dev/null https://api.github.com; then API_OK=1; fi
echo "      github.com: $([[ $GIT_OK -eq 1 ]] && echo 可达 || echo 不可达) | api.github.com: $([[ $API_OK -eq 1 ]] && echo 可达 || echo 不可达)"

if [[ -z "$TOKEN" ]]; then
  echo "[!] 未提供 GITHUB_TOKEN / GH_TOKEN" >&2
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    echo "      检测到 gh 已登录，尝试路径 A" >&2
  else
    echo "[3/4] 已停止：本地提交已完成，但推送需要认证。任选一种：" >&2
    echo "      a) 交互式登录： gh auth login   然后重跑本脚本" >&2
    echo "      b) 提供令牌：  GITHUB_TOKEN=ghp_xxx ./scripts/push_github.sh" >&2
    echo "      令牌需 repo 权限（fine-grained: Contents 读写 + Metadata 只读）" >&2
    exit 2
  fi
fi

if [[ $GIT_OK -eq 1 ]]; then
  echo "[3/4] 路径 A：git push"
  if [[ -n "$TOKEN" ]]; then
    export GH_TOKEN="$TOKEN"
  fi
  if ! gh repo view "$REPO" >/dev/null 2>&1; then
    gh repo create "$REPO" --public --source=. --remote=origin \
      --description "国家队资金三层监控看板：分层标注置信度，不提供个股买卖建议"
  fi
  git push -u origin main
  echo "[4/4] 推送完成"
  gh repo view "$REPO" --web 2>/dev/null || true
  exit 0
fi

if [[ $API_OK -eq 1 && -n "$TOKEN" ]]; then
  echo "[3/4] 路径 B：REST API 单提交推送（github.com 不可达）"
  export GITHUB_TOKEN="$TOKEN" GITHUB_REPO="$REPO" GITHUB_OWNER="$OWNER"
  python3 scripts/push_via_api.py
  echo "[4/4] 推送完成"
  exit 0
fi

echo "[ERROR] github.com 与 api.github.com 均不可达，无法推送。" >&2
echo "        本地提交已完成，网络恢复后重跑本脚本即可。" >&2
exit 3
