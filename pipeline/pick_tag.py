"""选出需要构建的上游最新 tag。

用法（CI）：
    python pipeline/pick_tag.py \
        --remote https://github.com/ok-oldking/ok-wuthering-waves.git \
        --built-remote https://github.com/<owner>/<repo>.git

输出（写入 GITHUB_OUTPUT，同时打印）：
    tag=v3.8.16

规则：
  - 只认 vX.Y.Z / vX.Y.Z-beta.N 形式；
  - 默认只取"最新稳定版"（无后缀），没有稳定版才退到 beta；
  - 已经构建过的（存在 kl-built/<tag> 标记 tag）跳过，除非 --force。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?$")


def ls_remote_tags(remote: str) -> list[str]:
    p = subprocess.run(["git", "ls-remote", "--tags", remote],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        raise SystemExit(f"git ls-remote failed: {remote}")
    tags = set()
    for line in p.stdout.splitlines():
        ref = line.split("\t", 1)[-1].strip()
        if ref.startswith("refs/tags/"):
            tags.add(ref[len("refs/tags/"):].removesuffix("^{}"))
    return sorted(tags)


def version_key(tag: str):
    m = TAG_RE.match(tag)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)),
            0 if m.group(4) is None else -1, m.group(4) or "")


def emit(tag: str) -> None:
    line = f"tag={tag}"
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(line)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", required=True)
    ap.add_argument("--built-remote", default="")
    ap.add_argument("--include-beta", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--override", default="", help="手动指定 tag，直接输出")
    args = ap.parse_args()

    if args.override:
        emit(args.override)
        return 0

    tags = [t for t in ls_remote_tags(args.remote) if version_key(t)]
    if not tags:
        emit("")
        return 0

    stable = [t for t in tags if TAG_RE.match(t).group(4) is None]
    pool = stable if stable and not args.include_beta else tags
    latest = max(pool, key=version_key)

    built = set()
    if args.built_remote:
        try:
            for t in ls_remote_tags(args.built_remote):
                if t.startswith("kl-built/"):
                    built.add(t[len("kl-built/"):])
        except SystemExit:
            print("warning: 无法读取已构建标记，按未构建处理", file=sys.stderr)

    if latest in built and not args.force:
        print(f"latest={latest} already built")
        emit("")
        return 0

    print(f"latest={latest} built={sorted(built)}")
    emit(latest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
