"""Rebrand toolkit for a portable pyappify build.

Six layers covered:
  peb       - RTL_USER_PROCESS_PARAMETERS rewrite (cmdline, image path, cwd, env)
  envbrand  - environment block + Python-level env/argv synchronisation
  wndclass  - USER32 IAT detour of RegisterClass*/CreateWindowExW
  respaths  - resource root, temp/mutex/log/config naming
  litgen    - per-build string decoder generation (string-table decoder)
  apply     - source tree token rewriter + residual scanner
"""

from .brand import Brand, DEFAULT_BRAND_PATH  # noqa: F401
