#!/usr/bin/env python3
"""通过 GitHub REST API 推送整个仓库（单次提交）。

适用场景：github.com / git 协议不可达，但 api.github.com 可达。
凭据只从 GITHUB_TOKEN 环境变量读取，绝不打印、不落盘。

用法：
    GITHUB_TOKEN=ghp_xxx python3 scripts/push_via_api.py \
        [--repo national-team-monitor] [--owner someone] [--private]
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

import requests

# 允许覆盖 API 根地址：便于本地端到端验证与 GitHub Enterprise 场景。
# 默认仍为官方 api.github.com，正常推送无需设置。
API = os.getenv("GITHUB_API_BASE", "https://api.github.com").rstrip("/")
ROOT = Path(__file__).resolve().parents[1]

EXCLUDE_DIRS = {
    ".git", "data", "__pycache__", ".pytest_cache", ".venv", "venv",
    "node_modules", "dist", "build", ".idea", ".vscode", "htmlcov",
}
EXCLUDE_NAMES = {".env", ".DS_Store", ".coverage"}
EXCLUDE_SUFFIXES = {".pyc", ".db", ".db-shm", ".db-wal", ".log", ".tar.gz"}

DESCRIPTION = (
    "A股国家队资金三层监控看板：L1实测/L2估算/L3代理，"
    "带置信度的托底信号（推断非实锤），不提供个股买卖建议"
)
COMMIT_MESSAGE = "feat: 国家队资金三层监控看板（修订版提示词落地实现）"


def collect_files() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        parts = Path(rel).parts
        if any(part in EXCLUDE_DIRS for part in parts):
            continue
        if p.name in EXCLUDE_NAMES or any(rel.endswith(s) for s in EXCLUDE_SUFFIXES):
            continue
        if p.stat().st_size > 5 * 1024 * 1024:
            print(f"[warn] 跳过超大文件：{rel}", file=sys.stderr)
            continue
        out.append((rel, p))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.getenv("GITHUB_REPO", "national-team-monitor"))
    ap.add_argument("--owner", default=os.getenv("GITHUB_OWNER", ""))
    ap.add_argument("--private", action="store_true")
    args = ap.parse_args()

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        print("[ERROR] 缺少 GITHUB_TOKEN（或 GH_TOKEN）环境变量", file=sys.stderr)
        return 2

    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })

    # 0) 连通性与身份
    try:
        me = s.get(f"{API}/user", timeout=15)
        me.raise_for_status()
    except requests.RequestException as exc:
        print(f"[ERROR] api.github.com 不可达或 token 无效：{type(exc).__name__}", file=sys.stderr)
        return 3
    owner = args.owner or me.json()["login"]
    repo = args.repo
    base = f"{API}/repos/{owner}/{repo}"
    print(f"[1/6] 身份确认：{owner} / {repo}")

    # 1) 创建仓库（已存在则复用）
    r = s.post(f"{API}/user/repos", json={
        "name": repo, "description": DESCRIPTION,
        "private": args.private, "auto_init": False,
    }, timeout=20)
    if r.status_code == 201:
        print("[2/6] 仓库已创建")
    elif r.status_code == 422:
        print("[2/6] 仓库已存在，复用")
    else:
        # GitHub App 安装令牌 / 细粒度令牌无「创建个人仓库」权限
        # （POST /user/repos 返回 403 Resource not accessible by integration），
        # 但对已存在的仓库有完整写权限。此时检查目标仓库是否已由用户创建。
        print(f"[2/6] 自动建仓不可用（HTTP {r.status_code}），检查仓库是否已存在…")
        chk = s.get(base, timeout=15)
        if chk.status_code == 200:
            print("      仓库已存在，复用（跳过创建）")
        else:
            print(f"[ERROR] 令牌无建仓权限，且目标仓库 {owner}/{repo} 不存在"
                  f"（GET /repos 返回 HTTP {chk.status_code}）", file=sys.stderr)
            print("       原因：GitHub App / 细粒度令牌不能新建个人仓库，"
                  "只能向已存在的仓库推送。", file=sys.stderr)
            print("       处理：用下面链接一键建空仓库（名称已预填），"
                  "保持不勾选任何初始化文件，然后重跑本脚本：", file=sys.stderr)
            print(f"       https://github.com/new?name={repo}"
                  "&description=A%E8%82%A1%E5%9B%BD%E5%AE%B6%E9%98%9F%E8%B5%84%E9%87%91%E4%B8%89%E5%B1%82%E7%9B%91%E6%8E%A7%E7%9C%8B%E6%9D%BF"
                  "&visibility=public", file=sys.stderr)
            print("       或改用具备 repo 权限的 classic PAT（可自动建仓）。",
                  file=sys.stderr)
            return 4

    # 2) 现有 main 分支（用于增量提交）
    ref = s.get(f"{base}/git/ref/heads/main", timeout=15)
    parent_sha: str | None = None
    base_tree: str | None = None
    if ref.status_code == 200:
        parent_sha = ref.json()["object"]["sha"]
        c = s.get(f"{base}/git/commits/{parent_sha}", timeout=15)
        c.raise_for_status()
        base_tree = c.json()["tree"]["sha"]
        print("[3/6] 检出已有 main 分支（增量提交）")
    else:
        print("[3/6] 空仓库，创建首个提交")

    files = collect_files()
    print(f"[4/6] 上传 {len(files)} 个文件的 blob")
    tree: list[dict] = []
    for i, (rel, path) in enumerate(files, 1):
        content = path.read_bytes()
        b = s.post(f"{base}/git/blobs", json={
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        }, timeout=30)
        if not b.ok:
            print(f"[ERROR] blob 上传失败 {rel}: HTTP {b.status_code}", file=sys.stderr)
            return 5
        tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": b.json()["sha"]})
        if i % 10 == 0 or i == len(files):
            print(f"       {i}/{len(files)}")

    # 3) tree → commit → ref
    payload: dict = {"tree": tree}
    if base_tree:
        payload["base_tree"] = base_tree
    t = s.post(f"{base}/git/trees", json=payload, timeout=30)
    t.raise_for_status()

    c = s.post(f"{base}/git/commits", json={
        "message": COMMIT_MESSAGE,
        "tree": t.json()["sha"],
        "parents": [parent_sha] if parent_sha else [],
    }, timeout=30)
    if not c.ok:
        print(f"[ERROR] 创建提交失败：HTTP {c.status_code} {c.text[:200]}", file=sys.stderr)
        return 6
    commit_sha = c.json()["sha"]

    if parent_sha:
        u = s.patch(f"{base}/git/refs/heads/main", json={"sha": commit_sha}, timeout=20)
    else:
        u = s.post(f"{base}/git/refs", json={
            "ref": "refs/heads/main", "sha": commit_sha,
        }, timeout=20)
    if not u.ok:
        print(f"[ERROR] 更新分支失败：HTTP {u.status_code} {u.text[:200]}", file=sys.stderr)
        return 7

    print(f"[5/6] 提交完成：{commit_sha[:8]}")
    print(f"[6/6] 已推送 → https://github.com/{owner}/{repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
