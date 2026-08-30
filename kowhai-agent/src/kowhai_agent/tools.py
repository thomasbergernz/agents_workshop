"""The tools, as closures over a database rather than module globals.

Same three tools as the workshop notebook, same guardrails, but each spec is
generated from the signature by @tool instead of being maintained by hand.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

import duckdb

from .data import Database
from .tooling import Toolbox, tool

TIME_COLUMNS = ("submit_ts", "eligible_ts", "start_ts", "end_ts", "ts")
LOOKUP_COLUMNS = ("account", "project_name", "institution", "partition", "state",
                  "job_name", "user", "last_reason")

LookupColumn = Literal["account", "project_name", "institution", "partition",
                       "state", "job_name", "user", "last_reason"]

_TIME_FILTER = re.compile(r"\b(" + "|".join(TIME_COLUMNS) + r")\s*(>=|>|<=|<|between)", re.IGNORECASE)

# Comments and string literals are blanked before the time-filter search, so a
# predicate cannot hide in `-- submit_ts > 1` or in 'submit_ts > x'. The literal
# alternative comes first, so a `--` inside a string is not read as a comment.
_COMMENT_OR_LITERAL = re.compile(r"'(?:[^']|'')*'|--[^\n]*|/\*.*?\*/", re.DOTALL)


def _single_select(sql: str) -> str | None:
    """An error message unless `sql` is exactly one SELECT statement, else None.

    A prefix check only inspects the start of the string, so `SELECT 1; DROP
    VIEW jobs` passed it. DuckDB's own parser counts the statements and names
    their type, which is the same question asked of the thing that will run them.
    """
    try:
        statements = duckdb.extract_statements(sql)
    except Exception as exc:
        return f"Error: {exc}"
    if len(statements) != 1:
        return ("Error: send exactly one statement. Anything after a semicolon "
                "is rejected, including a second SELECT.")
    # `!=`, not `is not`: duckdb's StatementType is a pybind11 enum and hands
    # back a fresh object on each access, so identity never holds.
    if statements[0].type != duckdb.StatementType.SELECT:
        return "Error: only SELECT queries are allowed. This tool cannot modify data."
    return None


def load_inventory(path: Path) -> dict[str, dict[str, Any]]:
    """Partition hardware and charging policy: `scontrol show partition` plus finance."""
    return json.loads(path.read_text(encoding="utf-8"))


def build_toolbox(db: Database, inventory: dict[str, dict[str, Any]],
                  max_rows: int = 50) -> Toolbox:
    """Wire the tools to one database and one cluster inventory."""
    window = (f"{db.window_start:%Y-%m-%d %H:%M}", f"{db.window_end:%Y-%m-%d %H:%M}")

    @tool
    def partition_info(partition: str) -> str:
        """Hardware and policy for one Slurm partition: node count, cores and memory
        per node, GPU model, maximum walltime, and the charging rate. Use it to judge
        whether a job's request was reasonable or what it cost.

        partition: Partition name, for example 'large' or 'gpu'.
        """
        details = inventory.get(partition)
        if details is None:
            return (f"Unknown partition '{partition}'. "
                    f"Known partitions: {', '.join(inventory)}.")
        lines = [f"partition: {partition}"]
        lines += [f"{k}: {v}" for k, v in details.items()]
        cores = details["nodes"] * details["cpus_per_node"]
        lines.append(f"total cores: {cores}")
        lines.append(f"core-hours available per day: {cores * 24:,}")
        return "\n".join(lines)

    @tool
    def run_sql(sql: str) -> str:
        """Run one read-only DuckDB SELECT query against the Slurm accounting data.
        Every query must filter on a timestamp column. Results are capped, so
        aggregate rather than selecting raw rows.

        sql: A single SELECT statement.
        """
        rejection = _single_select(sql)
        if rejection is not None:
            return rejection
        if not _TIME_FILTER.search(_COMMENT_OR_LITERAL.sub(" ", sql)):
            return (
                "Error: every query must filter on a timestamp column "
                f"({', '.join(TIME_COLUMNS)}).\n"
                "Add a predicate such as:\n"
                f"  WHERE submit_ts >= TIMESTAMP '{window[0]}'\n"
                f"    AND submit_ts <  TIMESTAMP '{window[1]}'\n"
                f"The data covers {window[0]} to {window[1]} UTC."
            )
        try:
            frame = db.sql(sql).df()
        except Exception as exc:
            return f"Error: {exc}"
        if frame.empty:
            return ("0 rows. The query is valid but nothing matched it. Check your "
                    "filter values with list_values before concluding the answer is zero.")
        if len(frame) > max_rows:
            return (frame.head(max_rows).to_markdown(index=False) +
                    f"\n\n[truncated at {max_rows} rows. Aggregate, or add ORDER BY and LIMIT]")
        return frame.to_markdown(index=False)

    @tool
    def list_values(column: LookupColumn, contains: str = "") -> str:
        """List the real distinct values of a dimension column before filtering on it.
        Use this whenever the user names a project, institution, partition or job in
        prose, because the stored spelling and punctuation will not match what they typed.

        column: Which dimension column to list.
        contains: Case-insensitive fragment to filter by.
        """
        if column not in LOOKUP_COLUMNS:
            return f"Error: column must be one of {list(LOOKUP_COLUMNS)}."
        frame = db.connection.execute(
            f"SELECT DISTINCT {column} AS value FROM jobs "
            f"WHERE {column} ILIKE ? ORDER BY 1 LIMIT 100", [f"%{contains}%"]
        ).df()
        if frame.empty:
            return f"No {column} value contains '{contains}'. Try a shorter fragment."
        suffix = "\n[first 100 only]" if len(frame) == 100 else ""
        return "\n".join(frame["value"].astype(str)) + suffix

    return Toolbox.of(partition_info, run_sql, list_values)
