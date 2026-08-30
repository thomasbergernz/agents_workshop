#!/usr/bin/env python3
"""Collect CVE IDs for everything this repository asks people to install.

There are two dependency sets here and they are not the same:

  1. kowhai-agent's uv.lock -- 32 fully resolved runtime packages, which is what
     the package and the scheduled advisory job run against.
  2. The notebook's Part 0 cell -- a separate `pip install` line, and the one
     every workshop attendee actually runs in Colab. Nothing else tracks it:
     Dependabot does not read a pip install inside a notebook cell, and an SBOM
     of the package would not cover it.

Both are queried against OSV, and the OSV ids are resolved to their CVE aliases
because that is what the prioritizer downstream consumes. Packages the notebook
installs without a version are reported rather than skipped quietly -- an
unpinned dependency is not a clean scan, it is an unscannable one.

Standard library only, so it runs on a bare runner.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/"
PINNED = re.compile(r"^([A-Za-z0-9._-]+)==([^\s;]+)")
BARE = re.compile(r"^[A-Za-z0-9._-]+$")


def post(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def from_requirements(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    pinned, unpinned = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        match = PINNED.match(line)
        if match:
            pinned.append((match.group(1), match.group(2)))
        elif BARE.match(line):
            unpinned.append(line)
    return pinned, unpinned


def from_notebook(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """The `!pip install` line in the notebook's setup cell."""
    notebook = json.loads(path.read_text(encoding="utf-8"))
    pinned, unpinned = [], []
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
            continue
        # Join shell line-continuations first. Without this a wrapped pip line
        # is read only as far as the backslash, and every package after it is
        # silently absent from the scan rather than reported as unscanned.
        source = re.sub(r"\\\s*\n\s*", " ", "".join(cell["source"]))
        for line in source.splitlines():
            if "pip install" not in line:
                continue
            for token in line.split():
                if token.startswith("-") or token in ("!pip", "pip", "install"):
                    continue
                match = PINNED.match(token)
                if match:
                    pinned.append((match.group(1), match.group(2)))
                elif BARE.match(token):
                    unpinned.append(token)
    return pinned, unpinned


def advisories(packages: list[tuple[str, str]]) -> dict[str, set[str]]:
    """{package==version: {osv id, ...}} for everything with a known advisory."""
    if not packages:
        return {}
    queries = [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v} for n, v in packages]
    results = post(OSV_BATCH, {"queries": queries})["results"]
    found: dict[str, set[str]] = {}
    for (name, version), result in zip(packages, results):
        ids = {v["id"] for v in (result.get("vulns") or [])}
        if ids:
            found[f"{name}=={version}"] = ids
    return found


def cve_aliases(osv_ids: set[str]) -> set[str]:
    """OSV ids are often GHSA-*; the prioritizer needs CVE-*."""
    cves = set()
    for osv_id in sorted(osv_ids):
        if osv_id.startswith("CVE-"):
            cves.add(osv_id)
            continue
        try:
            record = get(OSV_VULN + osv_id)
        except Exception as exc:                      # noqa: BLE001
            print(f"::warning::could not resolve {osv_id}: {exc}", file=sys.stderr)
            continue
        aliases = {a for a in record.get("aliases", []) if a.startswith("CVE-")}
        if aliases:
            cves |= aliases
        else:
            print(f"::warning::{osv_id} has no CVE alias, so it cannot be prioritized",
                  file=sys.stderr)
    return cves


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, action="append", default=[],
                        metavar="LABEL=PATH", help="a pinned requirements file to scan")
    parser.add_argument("--notebook", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, default=Path("cves.txt"))
    args = parser.parse_args()

    sets: list[tuple[str, list[tuple[str, str]], list[str]]] = []
    for entry in args.requirements:
        label, _, path = str(entry).partition("=")
        sets.append((label, *from_requirements(Path(path))))
    for path in args.notebook:
        sets.append((f"{path.name} (Part 0 cell)", *from_notebook(path)))

    lines = ["| Dependency set | Pinned | Unpinned | With advisories |",
             "|---|---:|---:|---:|"]
    all_osv: set[str] = set()
    unscannable: list[str] = []
    for label, pinned, unpinned in sets:
        found = advisories(pinned)
        for ids in found.values():
            all_osv |= ids
        unscannable += [f"{label}: {name}" for name in unpinned]
        lines.append(f"| {label} | {len(pinned)} | {len(unpinned)} | {len(found)} |")
        for package, ids in sorted(found.items()):
            print(f"{label}: {package} -> {', '.join(sorted(ids))}")

    cves = cve_aliases(all_osv)
    args.out.write_text("\n".join(sorted(cves)) + ("\n" if cves else ""), encoding="utf-8")

    summary = ["## Dependency advisories", "", *lines, ""]
    if unscannable:
        summary += ["", "### Installed without a version, so not scanned", "",
                    "An unpinned dependency is not a clean result, it is an absent one.", ""]
        summary += [f"- `{name}`" for name in unscannable]
    if not cves:
        summary += ["", "No known advisories in any pinned dependency."]
    summary += ["", f"**{len(cves)} CVE id(s) passed to prioritization.**"]

    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(step_summary).write_text("\n".join(summary), encoding="utf-8")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"cve-count={len(cves)}\n")
            handle.write(f"unpinned-count={len(unscannable)}\n")
    print("\n".join(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
