#!/usr/bin/env python3
"""
Operational synchronization script for static mirrors.

Synchronizes:
- index.html (repository root)
- docs/index.html (GitHub Pages distribution)
with INSTITUTIONAL_PORTAL_HTML defined in bot/institutional_portal.py.
"""
from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bot.institutional_portal import INSTITUTIONAL_PORTAL_HTML


def sync_static_mirrors() -> tuple[str, str]:
    """Synchronize index.html and docs/index.html with INSTITUTIONAL_PORTAL_HTML."""
    index_path = os.path.join(PROJECT_ROOT, "index.html")
    docs_path = os.path.join(PROJECT_ROOT, "docs", "index.html")

    os.makedirs(os.path.dirname(docs_path), exist_ok=True)

    with open(index_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(INSTITUTIONAL_PORTAL_HTML)

    with open(docs_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(INSTITUTIONAL_PORTAL_HTML)

    return index_path, docs_path


def main() -> int:
    index_path, docs_path = sync_static_mirrors()
    num_bytes = len(INSTITUTIONAL_PORTAL_HTML.encode("utf-8"))
    print(f"[SYNC] Successfully mirrored INSTITUTIONAL_PORTAL_HTML ({num_bytes} bytes)")
    print(f"  -> {index_path}")
    print(f"  -> {docs_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
