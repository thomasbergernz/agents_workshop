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


def advertised_test_counts() -> list[tuple[Path, int]]:
    """Both READMEs quote how many tests there are. They drift every time one
    is added, and a number nobody checks is worse than no number."""
    found = []
    for readme in (ROOT / "README.md", ROOT / "kowhai-agent" / "README.md"):
        for match in re.finditer(r"#\s*(\d+) tests, no network", readme.read_text(encoding="utf-8")):
            found.append((readme, int(match.group(1))))
    return found


def collected_tests() -> int:
    import subprocess
    out = subprocess.run(["uv", "run", "pytest", "--collect-only", "-q"],
                         cwd=ROOT / "kowhai-agent", capture_output=True, text=True).stdout
    match = re.search(r"(\d+) tests? collected", out)
    if not match:
        raise SystemExit(f"could not read a test count from pytest:\n{out[-500:]}")
    return int(match.group(1))


def main() -> int:
    notebook = ROOT / "kowhai_slurm_agents_workshop.ipynb"
    readme = ROOT / "README.md"
    failed = False

    expected = notebook_install_line(notebook)
    if expected not in normalise(readme.read_text(encoding="utf-8")):
        print(f"::error file=README.md::README does not quote the notebook's current "
              f"install line.\nnotebook has: {expected}", file=sys.stderr)
        failed = True
    else:
        print(f"README matches the notebook: {expected}")

    actual = collected_tests()
    for path, claimed in advertised_test_counts():
        name = path.relative_to(ROOT)
        if claimed != actual:
            print(f"::error file={name}::says {claimed} tests, pytest collects {actual}",
                  file=sys.stderr)
            failed = True
        else:
            print(f"{name} test count matches: {actual}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
