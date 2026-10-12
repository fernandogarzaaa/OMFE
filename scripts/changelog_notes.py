"""Print the CHANGELOG.md section for a version (used by .github/workflows/release.yml).

    python scripts/changelog_notes.py 1.0.0 > notes.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def notes(version: str, text: str) -> str:
    version = version.removeprefix("v")
    pattern = re.compile(rf"^## \[?{re.escape(version)}\]?.*?$(.*?)(?=^## |\Z)", re.M | re.S)
    m = pattern.search(text)
    if not m:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    return m.group(1).strip() + "\n"


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    sys.stdout.write(notes(sys.argv[1], (root / "CHANGELOG.md").read_text(encoding="utf-8")))
