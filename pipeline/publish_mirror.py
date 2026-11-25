"""把构建树发布为孤儿分支 + tag（供 KL 自建更新器使用）。

tag 一律为四段纯数字：v<上游三段>.<构建序号>，例如 v3.8.15.1、v3.8.15.2。
同一上游版本可重复发布，序号自动取远端已有的最大值 +1；历史的
v3.8.15-kl.N 不参与比较，也不会被覆盖。

用法（CI）：
    python pipeline/publish_mirror.py \
        --tree build --branch kl-mirror \
        --base-tag v3.8.15 --repo owner/name

凭据从环境变量读取（默认 GITHUB_TOKEN），不会打印出来。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

NUMERIC_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)\.(\d+)$")


def remote_numeric_tags(url: str, base: str) -> list[int]:
    """返回远端 base 版本下已存在的四段 tag 序号列表。"""
    p = subprocess.run(["git", "ls-remote", "--tags", url], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"git ls-remote failed: {p.stderr.strip()}")
    numbers = []
    for line in p.stdout.splitlines():
        ref = line.split("\t", 1)[-1].strip()
        if not ref.startswith("refs/tags/") or ref.endswith("^{}"):
            continue
        tag = ref[len("refs/tags/"):]
        m = NUMERIC_TAG_RE.match(tag)
        if m and f"v{m.group(1)}.{m.group(2)}.{m.group(3)}" == base:
            numbers.append(int(m.group(4)))
    return numbers


def next_numeric_tag(url: str, base: str) -> str:
    numbers = remote_numeric_tags(url, base)
    return f"{base}.{(max(numbers) + 1) if numbers else 1}"


def run(cmd: list, cwd: Path) -> None:
    p = subprocess.run([str(c) for c in cmd], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(map(str, cmd))}\n"
                           f"{p.stdout}\n{p.stderr}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--branch", default="kl-mirror")
    ap.add_argument("--tag", default="", help="显式四段 tag；留空则按 --base-tag 自动递增")
    ap.add_argument("--base-tag", default="", help="上游三段 tag，例如 v3.8.15")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--token-env", default="GITHUB_TOKEN")
    ap.add_argument("--message", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tree = Path(args.tree).resolve()
    if not tree.is_dir():
        raise SystemExit(f"tree not found: {tree}")
    if not args.repo and not args.dry_run:
        raise SystemExit("--repo (owner/name) required")

    token = os.environ.get(args.token_env, "")
    if not token and not args.dry_run:
        raise SystemExit(f"environment {args.token_env} is required to publish")

    base = args.base_tag.strip()
    if not base and args.tag:
        if NUMERIC_TAG_RE.match(args.tag):
            base = ".".join(args.tag.split(".")[:3])
        elif "-kl." in args.tag:
            base = args.tag.rsplit("-kl.", 1)[0]
    if not base:
        raise SystemExit("--base-tag (or a numeric/legacy --tag) is required")
    if not re.match(r"^v\d+\.\d+\.\d+$", base):
        raise SystemExit(f"base tag must look like v3.8.15, got: {base}")

    repo_url = f"https://x-access-token:{token}@github.com/{args.repo}.git" if args.repo else ""
    if args.dry_run:
        tag = args.tag or f"{base}.1"
        if not args.tag and repo_url:
            tag = next_numeric_tag(repo_url, base)
    else:
        tag = args.tag or next_numeric_tag(repo_url, base)
    if not NUMERIC_TAG_RE.match(tag):
        raise SystemExit(f"publish tag must be four numeric parts, got: {tag}")

    marker = f"kl-built/{base}"
    message = args.message or f"KL mirror {tag} from {base}"

    tmp = Path(tempfile.mkdtemp(prefix="kl-publish-"))
    try:
        for item in tree.iterdir():
            if item.name == ".git":
                continue
            target = tmp / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)

        run(["git", "init"], tmp)
        run(["git", "config", "user.name", "kl-pipeline"], tmp)
        run(["git", "config", "user.email", "kl-pipeline@users.noreply.github.com"], tmp)
        run(["git", "checkout", "-b", args.branch], tmp)
        run(["git", "add", "-A"], tmp)
        run(["git", "commit", "-m", message], tmp)
        run(["git", "tag", "-f", tag], tmp)
        run(["git", "tag", "-f", marker], tmp)

        if args.dry_run:
            print(f"dry-run: tree={tree} branch={args.branch} tag={tag} marker={marker}")
            return 0

        run(["git", "remote", "add", "origin", repo_url], tmp)
        run(["git", "push", "--force", "origin", f"HEAD:refs/heads/{args.branch}"], tmp)
        run(["git", "push", "--force", "origin", f"refs/tags/{tag}"], tmp)
        run(["git", "push", "--force", "origin", f"refs/tags/{marker}"], tmp)
        print(f"published {tag} -> {args.repo}:{args.branch} (marker {marker})")
        return 0
    finally:
        def _onexc(func, target, exc):
            try:
                os.chmod(target, 0o700)
                func(target)
            except OSError:
                pass

        shutil.rmtree(tmp, onexc=_onexc)


if __name__ == "__main__":
    raise SystemExit(main())
