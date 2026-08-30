"""The system prompt lives in markdown files, not in Python string literals.

Schema cards and domain notes are content: a colleague who knows the cluster but
not Python should be able to edit them, they should be reviewed on their own
merits in a pull request, and you should be able to diff how your institutional
knowledge changed over a year. Files are loaded in filename order, so the numeric
prefixes set the order they reach the model.
"""
from __future__ import annotations

from pathlib import Path


def _describes_a_missing_table(text: str, tables: list[str] | None) -> bool:
    """True when a card documents a table that was never loaded.

    sched_15m.parquet is optional, but its card went to the model regardless,
    so the model was told about a table it would then fail to query.
    """
    if tables is None:
        return False
    for line in text.splitlines():
        if line.startswith("## Table:"):
            return line.split(":", 1)[1].strip() not in tables
    return False


def load_context(context_dir: Path, tables: list[str] | None = None) -> str:
    """Concatenate every .md file in the context directory, in filename order.

    Pass `tables` to drop the schema card for any table that is not loaded.
    """
    if not context_dir.is_dir():
        raise SystemExit(f"No context directory at {context_dir}")
    parts = [text for text in
             (p.read_text(encoding="utf-8").strip() for p in sorted(context_dir.glob("*.md")))
             if not _describes_a_missing_table(text, tables)]
    if not parts:
        raise SystemExit(f"No .md context files in {context_dir}")
    return "\n\n".join(parts) + "\n"


def context_files(context_dir: Path) -> list[tuple[str, int]]:
    """(filename, word count) for each context file, for `kowhai context`."""
    return [(p.name, len(p.read_text(encoding="utf-8").split()))
            for p in sorted(context_dir.glob("*.md"))]
