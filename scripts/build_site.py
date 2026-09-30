"""Assemble the static GitHub Pages site: public/* + snapshots/* -> _site/.
Snapshot files are copied byte-for-byte so published hashes stay valid."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(out: Path = ROOT / "_site") -> Path:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "public", out)
    shutil.copytree(ROOT / "snapshots", out / "snapshots",
                    ignore=shutil.ignore_patterns("*.tmp"))
    (out / ".nojekyll").write_bytes(b"")
    return out


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_site"))
