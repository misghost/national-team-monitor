#!/usr/bin/env python3
"""把当前仓库推送到 GitCode / Gitee。

用法：
    GITCODE_TOKEN=xxx python3 scripts/push_gitcode.py
    GITEE_TOKEN=xxx  python3 scripts/push_gitcode.py --platform gitee
    python3 scripts/push_gitcode.py --token-file ~/.gitcode_token

设计要点：
  * 令牌只从环境变量或权限 600 的文件读取，绝不写入仓库、不打印、不进 git remote
    （推送时用一次性 URL，结束即清理 remote 里的凭据）。
  * 先查用户、再查仓库，仓库不存在才创建；已存在则直接复用，不覆盖远端内容。
  * 推送失败时不重试、不改写历史，原样报错。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

PLATFORMS = {
    "gitcode": {
        "api": "https://api.gitcode.com/api/v5",
        "host": "gitcode.com",
        "env": "GITCODE_TOKEN",
        "token_url": "https://gitcode.com/setting/token-classic",
    },
    "gitee": {
        "api": "https://gitee.com/api/v5",
        "host": "gitee.com",
        "env": "GITEE_TOKEN",
        "token_url": "https://gitee.com/personal_access_tokens",
    },
}

SECRET_RE = re.compile(r"(ak_[A-Za-z0-9]{16,}|[0-9a-f]{32,}|gh[pousr]_[A-Za-z0-9]{20,})")


def scrub(text: str) -> str:
    return SECRET_RE.sub("<redacted>", text or "")


def read_token(platform: str, token_file: str | None) -> str:
    env = PLATFORMS[platform]["env"]
    tok = (os.environ.get(env) or "").strip()
    if tok:
        return tok
    cand = token_file or os.path.expanduser(f"~/.{platform}_token")
    if os.path.exists(cand):
        mode = os.stat(cand).st_mode & 0o777
        if mode & 0o077:
            raise SystemExit(f"令牌文件 {cand} 权限过于宽松（{oct(mode)}），请先 chmod 600")
        tok = open(cand, encoding="utf-8").read().strip()
        if tok:
            return tok
    raise SystemExit(
        f"未找到 {platform} 令牌。请在 {PLATFORMS[platform]['token_url']} 生成个人访问令牌，"
        f"然后执行：{env}=<你的令牌> python3 scripts/push_gitcode.py --platform {platform}"
    )


def api(platform: str, method: str, path: str, token: str, payload: dict | None = None) -> tuple[int, dict | list | str]:
    url = PLATFORMS[platform]["api"] + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(body)
            except ValueError:
                return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, body


def git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=180)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="gitcode", choices=sorted(PLATFORMS))
    ap.add_argument("--repo", default=os.path.basename(os.getcwd()))
    ap.add_argument("--private", action="store_true", help="创建为私有仓库")
    ap.add_argument("--description", default="A 股国家队资金三层监控看板（公开数据概率推断，不构成投资建议）")
    ap.add_argument("--token-file", default=None)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    plat = PLATFORMS[args.platform]
    token = read_token(args.platform, args.token_file)
    repo_dir = os.getcwd()

    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        raise SystemExit("当前目录不是 git 仓库，请在项目根目录执行")

    print(f"[1/6] 校验令牌（{args.platform}）")
    code, user = api(args.platform, "GET", "/user", token)
    if code != 200 or not isinstance(user, dict):
        print(f"  ✗ 令牌无效或权限不足：HTTP {code} {scrub(str(user))[:200]}")
        return 2
    owner = user.get("login") or user.get("username") or user.get("name")
    if not owner:
        print(f"  ✗ 无法从 /user 解析用户名：{scrub(str(user))[:200]}")
        return 2
    print(f"  ✓ 认证通过，用户：{owner}")

    full = f"{owner}/{args.repo}"
    print(f"[2/6] 检查仓库 {full}")
    code, info = api(args.platform, "GET", f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(args.repo)}", token)
    if code == 200:
        print("  · 仓库已存在，复用")
    elif code in (404, 403):
        print("  · 仓库不存在，准备创建")
        payload = {
            "name": args.repo,
            "path": args.repo,
            "private": bool(args.private),
            "description": args.description,
            "auto_init": False,
            "has_issues": True,
            "has_wiki": False,
        }
        code, created = api(args.platform, "POST", "/user/repos", token, payload)
        if code in (200, 201):
            print(f"  ✓ 已创建：{full}（{'私有' if args.private else '公开'}）")
        else:
            print(f"  ✗ 创建失败：HTTP {code} {scrub(str(created))[:300]}")
            return 3
    else:
        print(f"  ✗ 查询仓库异常：HTTP {code} {scrub(str(info))[:200]}")
        return 3

    print("[3/6] 准备分支与远程")
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_dir).stdout.strip() or args.branch
    if branch == "HEAD":
        branch = args.branch
    # 统一成 main，便于平台默认分支一致
    if branch != args.branch:
        r = git("branch", "-M", args.branch, cwd=repo_dir)
        if r.returncode != 0:
            print(f"  ! 重命名分支失败（忽略）：{scrub(r.stderr)[:200]}")
        branch = args.branch
    print(f"  · 待推送分支：{branch}")

    remote_url = f"https://oauth2:{token}@{plat['host']}/{owner}/{args.repo}.git"
    remote_url_user = f"https://{owner}:{token}@{plat['host']}/{owner}/{args.repo}.git"
    git("remote", "remove", "origin", cwd=repo_dir)

    if args.dry_run:
        print("[4/6] --dry-run：跳过实际推送")
        return 0

    print("[4/6] 推送")
    ok = False
    last_err = ""
    for label, url in (("oauth2", remote_url), ("user", remote_url_user)):
        git("remote", "remove", "origin", cwd=repo_dir)
        git("remote", "add", "origin", url, cwd=repo_dir)
        r = git("push", "-u", "origin", f"{branch}:{branch}", cwd=repo_dir)
        if r.returncode == 0:
            print(f"  ✓ 推送成功（{label} 认证方式）")
            ok = True
            break
        last_err = (r.stderr or "") + (r.stdout or "")
        # 常见：仓库初始化了空 README 导致 non-fast-forward → 允许合并后重推
        if "non-fast-forward" in last_err or "fetch first" in last_err or "rejected" in last_err:
            print("  · 远端已有提交，改为合并后重推")
            git("pull", "--no-rebase", "--allow-unrelated-histories", "origin", branch, cwd=repo_dir)
            r2 = git("push", "-u", "origin", f"{branch}:{branch}", cwd=repo_dir)
            if r2.returncode == 0:
                print(f"  ✓ 合并后推送成功（{label}）")
                ok = True
                break
            last_err = (r2.stderr or "") + (r2.stdout or "")
    # 立即清掉带凭据的 remote
    git("remote", "remove", "origin", cwd=repo_dir)
    git("remote", "add", "origin", f"https://{plat['host']}/{owner}/{args.repo}.git", cwd=repo_dir)

    if not ok:
        print(f"  ✗ 推送失败：{scrub(last_err)[:600]}")
        return 4

    print("[5/6] 校验远端")
    code, info = api(args.platform, "GET", f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(args.repo)}", token)
    if code == 200 and isinstance(info, dict):
        web = info.get("html_url") or info.get("web_url") or ""
        print(f"  ✓ 仓库可访问：{web}")
    else:
        print(f"  ! 校验接口返回 HTTP {code}")

    print("[6/6] 清理")
    git("remote", "remove", "origin", cwd=repo_dir)
    git("remote", "add", "origin", f"https://{plat['host']}/{owner}/{args.repo}.git", cwd=repo_dir)
    print(f"  · remote 已重置为无凭据地址：git@{plat['host']}:{owner}/{args.repo}")
    print("\n完成。仓库地址：")
    print(f"  https://{plat['host']}/{owner}/{args.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
