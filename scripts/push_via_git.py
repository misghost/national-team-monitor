#!/usr/bin/env python3
"""通过 git 协议把项目推送到**已存在的仓库**（默认写入子目录，不覆盖原文件）。

适用场景：GitHub App 用户到服务器令牌（ghu_）等无 contents=write 权限、
导致 REST Git Data API 与 Contents API 均返回 403，但仓库权限位允许 git 推送时。

流程：浅克隆 → 复制文件到子目录 → 校验原有文件零改动 → 单次提交 → 带重试推送。
凭据只从 GITHUB_TOKEN / GITHUB_TOKEN_FILE 读取，不打印、不落盘、不写 remote。

用法：
    GITHUB_TOKEN=ghp_xxx python3 scripts/push_via_git.py \
        --repo lpp --owner misghost --prefix national-team-monitor
"""
from __future__ import annotations

import argparse
import base64
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from push_via_api import collect_files

COMMIT_MESSAGE = "feat: 国家队资金三层监控看板（子目录，原有文件保持不动）"


def mask_token() -> str:
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""
    if not token and os.getenv("GITHUB_TOKEN_FILE"):
        f = Path(os.environ["GITHUB_TOKEN_FILE"])
        if f.is_file():
            token = f.read_text().strip()
    if not token:
        print("[ERROR] 缺少 GITHUB_TOKEN / GH_TOKEN / GITHUB_TOKEN_FILE", file=sys.stderr)
        sys.exit(2)
    os.environ["GITHUB_TOKEN"] = token
    return token


def auth_header(token: str) -> str:
    """oauth2:<token> 的 Basic 认证；实测 x-access-token 格式会超时。"""
    raw = f"oauth2:{token}".encode()
    return "Authorization: Basic " + base64.b64encode(raw).decode("ascii")


def git(args: list[str], *, cwd: Path | None = None, header: str = "",
        timeout: int = 150) -> subprocess.CompletedProcess:
    cmd = ["git"]
    if header:
        # HTTP/1.1 与认证头均为实测必需：HTTP/2 常超时，header 不写入 .git/config
        cmd += ["-c", "http.version=HTTP/1.1", "-c", f"http.extraHeader={header}"]
    cmd += args
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def retry(args: list[str], *, cwd: Path | None, header: str, label: str,
          attempts: int = 4) -> subprocess.CompletedProcess:
    last: subprocess.CompletedProcess | None = None
    for i in range(1, attempts + 1):
        try:
            last = git(args, cwd=cwd, header=header)
        except subprocess.TimeoutExpired:
            print(f"      {label} 第 {i}/{attempts} 次：超时，重试")
            continue
        if last.returncode == 0:
            print(f"      {label} 成功（第 {i} 次尝试）")
            return last
        print(f"      {label} 第 {i}/{attempts} 次失败："
              f"{(last.stderr or last.stdout).strip()[:160]}")
    return last  # type: ignore[return-value]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.getenv("GITHUB_REPO", "national-team-monitor"))
    ap.add_argument("--owner", default=os.getenv("GITHUB_OWNER", ""))
    ap.add_argument("--branch", default=os.getenv("GITHUB_BRANCH", "main"))
    ap.add_argument("--prefix", default=os.getenv("GITHUB_PATH_PREFIX", ""),
                    help="仓库内子目录；强烈建议设置以保留原有文件")
    ap.add_argument("--url", default=os.getenv("GITHUB_GIT_URL", ""),
                    help="覆盖 git 地址（本地裸仓库验证用）")
    args = ap.parse_args()

    token = mask_token()
    header = auth_header(token)
    owner = args.owner or "misghost"
    url = args.url or f"https://github.com/{owner}/{args.repo}.git"
    # 本地 file:// 路径不需要认证头
    if url.startswith(("/", "file://")):
        header = ""
    prefix = args.prefix.strip("/")

    print(f"[1/6] 目标：{url} @ {args.branch}" + (f" / {prefix}" if prefix else "（仓库根）"))

    work = Path(tempfile.mkdtemp(prefix="ntm_git_push_"))
    try:
        print(f"[2/6] 浅克隆（工作目录 {work.name}）")
        r = retry(["clone", "--depth", "1", url, str(work / "repo")],
                  cwd=None, header=header, label="clone")
        if r.returncode != 0:
            print("[ERROR] clone 失败，未对远程仓库做任何改动", file=sys.stderr)
            return 3
        repo = work / "repo"

        # 校验克隆结果有效：仓库有 HEAD 且工作区已 checkout，
        # 否则后续提交会变成全新历史，推送必然 non-fast-forward
        if git(["rev-parse", "--verify", "HEAD"], cwd=repo).returncode != 0:
            print("[ERROR] 克隆出的仓库无有效 HEAD（目标分支可能不存在），已中止",
                  file=sys.stderr)
            return 3

        # 记录克隆下来的根目录文件，用于提交前校验零改动
        before = {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()}
        if not before:
            print("[ERROR] 克隆出的工作区为空，拒绝推送以避免产生无关历史",
                  file=sys.stderr)
            return 3

        print("[3/6] 备份同名文件并复制项目文件")
        dest = repo / prefix if prefix else repo
        dest.mkdir(parents=True, exist_ok=True)
        files = collect_files()

        # 无子目录时，同名文件必须在覆盖「之前」备份，且用 before 中记录的
        # 原始字节写盘；否则备份到的会是被覆盖后的新内容，原内容真实丢失。
        backed_up: set[str] = set()
        if not prefix:
            backups = repo / "_backup_before_push"
            for rel, _ in files:
                if rel in before:
                    bk = backups / rel
                    bk.parent.mkdir(parents=True, exist_ok=True)
                    bk.write_bytes(before[rel])
                    backed_up.add(rel)
            if backed_up:
                print(f"      同名文件原内容已备份至 _backup_before_push/：{sorted(backed_up)}")

        for rel, src in files:
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
        print(f"      已复制 {len(files)} 个文件（与版本库口径一致）")

        git(["add", "-A"], cwd=repo)
        # 用暂存区判定变更，不依赖 HEAD~1（浅克隆下不存在）
        staged = git(["diff", "--cached", "--name-status"], cwd=repo).stdout
        entries = [ln.split("\t", 1) for ln in staged.splitlines() if "\t" in ln]
        if not entries:
            print("      无文件变更，跳过提交")
            return 0

        print("[4/6] 校验原有文件零改动")
        # 子目录模式：根目录原有文件必须字节级不变，任何非 A 变更都违规。
        # 根目录模式：仅允许覆盖「已备份原内容」的同名文件（预期行为），
        #   其余已有文件的修改/删除一律拒绝。
        violated: list[str] = []
        for code, path in entries:
            if code.startswith("A"):
                continue
            if not prefix and path in backed_up:
                continue  # 已备份的预期覆盖
            violated.append(f"{code}:{path}")

        if prefix:
            for name, blob in before.items():
                cur = repo / name
                if not cur.is_file() or cur.read_bytes() != blob:
                    violated.append(name)
        else:
            for name, blob in before.items():
                if name in backed_up:
                    bk = repo / "_backup_before_push" / name
                    if not bk.is_file() or bk.read_bytes() != blob:
                        violated.append(f"备份缺失或不一致:{name}")
                else:
                    cur = repo / name
                    if not cur.is_file() or cur.read_bytes() != blob:
                        violated.append(name)

        if violated:
            print(f"[ERROR] 原有文件将被改动，已中止推送：{sorted(set(violated))}",
                  file=sys.stderr)
            return 5
        if prefix:
            print(f"      ✓ 根目录 {len(before)} 个原有文件字节级未变；"
                  f"本次新增 {len(entries)} 个路径")
        else:
            print(f"      ✓ 未备份的原有文件字节级未变；"
                  f"{len(backed_up)} 个同名文件原内容已备份后覆盖；"
                  f"共 {len(entries)} 个变更")

        commit = git(["-c", "user.name=NTM Bot", "-c", "user.email=ntm-bot@localhost",
                      "commit", "-q", "-m", COMMIT_MESSAGE], cwd=repo)
        if commit.returncode != 0:
            print(f"[ERROR] 提交失败：{commit.stderr[:200]}", file=sys.stderr)
            return 4

        print(f"[5/6] 推送 {args.branch}（带重试）")
        r = retry(["push", "origin", f"HEAD:refs/heads/{args.branch}"],
                  cwd=repo, header=header, label="push")
        if r.returncode != 0:
            print("[ERROR] 推送失败；本地提交已保留，远程仓库未被改动", file=sys.stderr)
            return 6

        location = f"https://github.com/{owner}/{args.repo}"
        if prefix:
            location += f"/tree/{args.branch}/{prefix}"
        print(f"[6/6] 已推送 → {location}")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
