"""把构建树发布为孤儿分支 + tag（供 PyAppify 客户端更新）。

用法（CI）：
    python pipeline/publish_mirror.py \
        --tree build --branch kl-mirror \
        --tag v3.8.16-kl.1 --repo owner/name \
        --message "KL v3.8.16 build 1"

凭据从环境变量读取（默认 GITHUB_TOKEN），不会打印出来。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


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
    ap.add_argument("--tag", required=True)
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

    upstream_tag = args.tag.rsplit("-kl.", 1)[0]
    marker = f"kl-built/{upstream_tag}"
    message = args.message or f"KL mirror {args.tag} from {upstream_tag}"

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
        run(["git", "tag", "-f", args.tag], tmp)
        run(["git", "tag", "-f", marker], tmp)

        if args.dry_run:
            print(f"dry-run: tree={tree} branch={args.branch} tag={args.tag} marker={marker}")
            return 0

        url = f"https://x-access-token:{token}@github.com/{args.repo}.git"
        run(["git", "remote", "add", "origin", url], tmp)
        run(["git", "push", "--force", "origin", f"HEAD:refs/heads/{args.branch}"], tmp)
        run(["git", "push", "--force", "origin", f"refs/tags/{args.tag}"], tmp)
        run(["git", "push", "--force", "origin", f"refs/tags/{marker}"], tmp)
        print(f"published {args.tag} -> {args.repo}:{args.branch} (marker {marker})")
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
