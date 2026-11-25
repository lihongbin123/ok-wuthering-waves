"""扫描构建树，确认没有上游指纹残留。

用法：
    python pipeline/check_forbidden.py build

命中即退出码 1，CI 会因此不发布 tag。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TEXT_EXT = {".py", ".pyi", ".txt", ".md", ".json", ".yml", ".yaml", ".toml",
            ".cfg", ".ini", ".po", ".ts", ".c", ".h", ".css", ".html"}

PATTERNS = {
    "upstream_repo": re.compile(r"ok-wuthering-waves|ok-oldking|ok-ww-update"),
    "upstream_brand": re.compile(r"ok-script|OK-WW|ok-ww|ok_ww|OKWW|okww"),
    "upstream_package": re.compile(
        r"(?<![\w.])from\s+ok(?=[\s.])|(?<![\w.])import\s+ok\.|"
        r"(?<![\w.])ok\.(util|core|ui|gui|device|capture|task|feature|ocr|notification|"
        r"update|rotypes|alas|third_party|assets|test)\b"
    ),
    "old_brand": re.compile(r"Zephyrus|Kagerou|kagerou|(?<![\w.])kgr(?![\w])"),
}

# 允许保留的文件：协议/格式本身就叫 ok（如 gettext 域），但内容仍会被扫描
ALLOW_FILES = {
    "i18n/zh_CN/LC_MESSAGES/ok.po",
    "i18n/zh_TW/LC_MESSAGES/ok.po",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tree")
    args = ap.parse_args()
    root = Path(args.tree).resolve()

    hits = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXT:
            continue
        rel = p.relative_to(root).as_posix()
        if rel in ALLOW_FILES:
            continue
        try:
            text = p.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, pattern in PATTERNS.items():
            for m in pattern.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                line = text.splitlines()[line_no - 1].strip()
                hits.append(f"{rel}:{line_no}: [{name}] {line[:160]}")

    if hits:
        print(f"FORBIDDEN: {len(hits)} hit(s)")
        for h in hits[:80]:
            print(h)
        return 1
    print("check_forbidden: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
