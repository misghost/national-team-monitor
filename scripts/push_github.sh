#!/usr/bin/env bash
# 推送到 GitHub。
#
# 凭据优先级：GITHUB_TOKEN / GH_TOKEN 环境变量 → CodeBuddy GitHub 连接器 OAuth。
# 推送路径优先级：
#   A) api.github.com 认证通过 → REST API 单提交（自动建仓，最稳，不依赖 git 协议）
#   B) 仅 github.com 可达     → git push（需仓库已存在）
#
# 安全：令牌只经环境变量/命令替换传递，绝不打印、绝不写入 .git/config 或任何文件。
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REPO="${GITHUB_REPO:-national-team-monitor}"
OWNER="${GITHUB_OWNER:-}"
TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
PREFIX="${GITHUB_PATH_PREFIX:-}"   # 复用已有仓库时建议设置子目录，避免覆盖原文件
BRANCH="${GITHUB_BRANCH:-main}"

echo "[1/5] 本地 git 提交"
if [[ ! -d .git ]]; then
  git init -q
  git branch -M main
fi
if [[ -z "$(git config user.name 2>/dev/null || true)" ]]; then
  git config user.name "NTM Bot"
fi
if [[ -z "$(git config user.email 2>/dev/null || true)" ]]; then
  git config user.email "ntm-bot@localhost"
fi
git add -A
if git diff --cached --quiet; then
  echo "      无新增改动，复用现有提交 $(git rev-parse --short HEAD)"
else
  git commit -q -m "feat: 国家队资金三层监控看板（修订版提示词落地实现）"
  echo "      已创建本地提交 $(git rev-parse --short HEAD)"
fi

echo "[2/5] 获取 GitHub 凭据"
# 凭据文件优先于环境变量之外的所有来源：命令文本中只出现路径，不出现令牌明文。
if [[ -z "$TOKEN" && -n "${GITHUB_TOKEN_FILE:-}" ]]; then
  if [[ -f "${GITHUB_TOKEN_FILE}" ]]; then
    TOKEN="$(tr -d '[:space:]' < "${GITHUB_TOKEN_FILE}")"
    echo "      已从凭据文件读取（内容不打印）"
  else
    echo "[!] GITHUB_TOKEN_FILE 指向的文件不存在" >&2
    exit 2
  fi
fi
if [[ -z "$TOKEN" ]]; then
  CONNECTOR="${CODEBUDDY_GITHUB_CONNECTOR:-$HOME/.codebuddy/skills/github-connector/scripts/get_token.sh}"
  if [[ -f "$CONNECTOR" ]]; then
    echo "      未显式提供令牌，尝试 GitHub 连接器 OAuth…"
    # 子 shell 隔离：连接器脚本内含 set -e / return，避免其影响本脚本执行流。
    # stdout 只输出令牌（不打印、不落盘），直接赋给变量。
    TOKEN="$(bash -c '
      source "$1" github >/dev/null 2>&1 || true
      printf "%s" "${GITHUB_TOKEN:-}"
    ' _ "$CONNECTOR")"
  fi
fi
if [[ -n "$TOKEN" ]]; then
  echo "      凭据已就绪（长度 ${#TOKEN}，值不打印）"
else
  echo "[!] 无法获取 GitHub 凭据，推送已停止。" >&2
  echo "    诊断：环境变量 GITHUB_TOKEN/GH_TOKEN 为空，且 GitHub 连接器未授权" >&2
  echo "          （网关返回：Connector \"github\" is not authorized for this user or project）" >&2
  echo "    本地提交已完成，凭据就绪后重跑本脚本即可，无需其他改动。" >&2
  echo "    任选一种方式：" >&2
  echo "      a) CodeBuddy 设置页 →「连接器」→ 授权 GitHub，然后： ./scripts/push_github.sh" >&2
  echo "      b) 凭据文件（推荐，令牌不进命令文本）：" >&2
  echo "         GITHUB_TOKEN_FILE=/path/to/token.txt ./scripts/push_github.sh" >&2
  echo "      c) 环境变量： GITHUB_TOKEN=ghp_xxx ./scripts/push_github.sh" >&2
  echo "         PAT 需 repo 权限（fine-grained：Contents 读写 + Metadata 只读）" >&2
  exit 2
fi

echo "[3/5] 确认身份与 API 连通性"
IDENTITY="$(curl -s --max-time 15 \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/user || true)"
LOGIN="$(printf '%s' "$IDENTITY" | python3 -c \
  'import sys,json; print(json.load(sys.stdin).get("login",""))' 2>/dev/null || true)"
if [[ -n "$LOGIN" ]]; then
  echo "      api.github.com 认证通过，身份：${LOGIN}"
  OWNER="${OWNER:-$LOGIN}"
else
  MSG="$(printf '%s' "$IDENTITY" | python3 -c \
    'import sys,json; print(json.load(sys.stdin).get("message","无响应"))' 2>/dev/null || echo 无响应)"
  echo "      API 认证未通过：${MSG}"
  echo "      若是 401/403，请检查令牌有效性或在设置页重新授权 GitHub 连接器。" >&2
fi

echo "[4/5] 推送"
URL="https://github.com/${OWNER:-$LOGIN}/${REPO}"
[[ -n "$PREFIX" ]] && URL="${URL}/tree/${BRANCH}/${PREFIX}"
EXPORT_TOKEN=("GITHUB_TOKEN=$TOKEN" "GITHUB_REPO=$REPO" "GITHUB_OWNER=$OWNER"
              "GITHUB_PATH_PREFIX=$PREFIX" "GITHUB_BRANCH=$BRANCH")

if [[ -n "$LOGIN" ]]; then
  echo "      路径 A：REST API 单提交（复用/创建仓库 ${OWNER}/${REPO} @ ${BRANCH}）"
  [[ -n "$PREFIX" ]] && echo "      子目录：${PREFIX}/（原有文件保持不动）"
  PRIVATE_ARGS=()
  [[ "${GITHUB_PRIVATE:-0}" == "1" ]] && PRIVATE_ARGS=(--private)
  set +e
  env "${EXPORT_TOKEN[@]}" python3 scripts/push_via_api.py "${PRIVATE_ARGS[@]}"
  API_RC=$?
  set -e
  if [[ $API_RC -eq 0 ]]; then
    echo "[5/5] 已推送 → ${URL}"
    exit 0
  fi
  # API 写权限不足（App 令牌无 contents=write）时，自动降级到 git 协议。
  # git 推送走仓库权限位，clone/push 带重试，且保持子目录零覆盖。
  if [[ $API_RC -eq 5 || $API_RC -eq 6 ]]; then
    echo "      API 写入被拒（退出码 ${API_RC}，通常为 contents=write 权限缺失）"
    echo "      路径 A2：降级到 git 协议子目录推送"
    set +e
    env "${EXPORT_TOKEN[@]}" python3 scripts/push_via_git.py
    GIT_RC=$?
    set -e
    if [[ $GIT_RC -eq 0 ]]; then
      echo "[5/5] 已推送 → ${URL}"
      exit 0
    fi
    echo "[ERROR] git 通道同样失败（退出码 ${GIT_RC}）。" >&2
    echo "        常见于令牌仅有读权限：GitHub App 用户令牌（ghu_）无 contents=write，" >&2
    echo "        需改用具备 repo 权限的 classic PAT（ghp_）后重跑本脚本。" >&2
    exit "$GIT_RC"
  fi
  exit "$API_RC"
fi

echo "[ERROR] api.github.com 认证失败且 git 通道不可用，本地提交已保留。" >&2
exit 3
