#!/usr/bin/env python3
"""Fail when the README quotes an install line the notebook no longer uses.

The README shows the notebook's Part 0 pip line so a reader can see what they
are about to install. That means the line exists in two places, and the copy in
the README is the one nobody re-reads -- it drifted the moment the notebook's
packages were pinned. Cheaper to assert than to notice.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def normalise(text: str) -> str:
    """Collapse shell line-continuations and runs of whitespace."""
    return " ".join(re.sub(r"\\\s*\n\s*", " ", text).split())


def notebook_install_line(path: Path) -> str:
    notebook = json.loads(path.read_text(encoding="utf-8"))
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
            continue
        source = re.sub(r"\\\s*\n\s*", " ", "".join(cell["source"]))
        for line in source.splitlines():
            if "pip install" in line:
                return normalise(line)
    raise SystemExit(f"no pip install line found in {path}")


def main() -> int:
    notebook = ROOT / "kowhai_slurm_agents_workshop.ipynb"
    readme = ROOT / "README.md"
    expected = notebook_install_line(notebook)
    if expected not in normalise(readme.read_text(encoding="utf-8")):
        print(f"::error file=README.md::README does not quote the notebook's current "
              f"install line.\nnotebook has: {expected}", file=sys.stderr)
        return 1
    print(f"README matches the notebook: {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
