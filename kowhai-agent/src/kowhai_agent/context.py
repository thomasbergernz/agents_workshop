"""The system prompt lives in markdown files, not in Python string literals.

Schema cards and domain notes are content: a colleague who knows the cluster but
not Python should be able to edit them, they should be reviewed on their own
merits in a pull request, and you should be able to diff how your institutional
knowledge changed over a year. Files are loaded in filename order, so the numeric
prefixes set the order they reach the model.
"""
from __future__ import annotations

from pathlib import Path


def load_context(context_dir: Path) -> str:
    """Concatenate every .md file in the context directory, in filename order."""
    if not context_dir.is_dir():
        raise SystemExit(f"No context directory at {context_dir}")
    parts = [p.read_text(encoding="utf-8").strip() for p in sorted(context_dir.glob("*.md"))]
    if not parts:
        raise SystemExit(f"No .md context files in {context_dir}")
    return "\n\n".join(parts) + "\n"


def context_files(context_dir: Path) -> list[tuple[str, int]]:
    """(filename, word count) for each context file, for `kowhai context`."""
    return [(p.name, len(p.read_text(encoding="utf-8").split()))
            for p in sorted(context_dir.glob("*.md"))]
