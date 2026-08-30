"""DuckDB over the Parquet exports.

The Parquet files are read once into tables rather than left behind lazy views,
because the connection then has no further use for the filesystem and can give
it up: `disabled_filesystems` closes `read_text`, `glob` and `COPY ... TO`, which
a SELECT-only guard does not, and `lock_configuration` stops a query turning
either setting back on. The cost is that the whole export is held in memory.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

# Bounds the model can neither raise nor spill around; see Database.open.
MEMORY_LIMIT = "2GB"
THREADS = 4


@dataclass
class Database:
    connection: duckdb.DuckDBPyConnection
    window_start: datetime
    window_end: datetime

    @classmethod
    def open(cls, data_dir: Path, memory_limit: str = MEMORY_LIMIT,
             threads: int = THREADS) -> Database:
        jobs = data_dir / "jobs.parquet"
        sched = data_dir / "sched_15m.parquet"
        if not jobs.exists():
            raise SystemExit(
                f"No jobs.parquet in {data_dir}. Generate the workshop dataset, or "
                "convert a real sacct export with sacct_to_parquet.py."
            )
        con = duckdb.connect(":memory:")
        con.execute("SET enable_progress_bar = false")
        con.execute(f"CREATE TABLE jobs AS SELECT * FROM '{jobs}'")
        if sched.exists():
            con.execute(f"CREATE TABLE sched_15m AS SELECT * FROM '{sched}'")
        lo, hi = con.sql("SELECT MIN(submit_ts), MAX(end_ts) FROM jobs").fetchone()

        # Order matters: lock_configuration freezes whatever is set at the time,
        # so every limit has to be in place before it. Without a memory_limit
        # the ceiling is the host's RAM, and because the filesystem is about to
        # go away there is no spilling to fall back on -- so an expensive query
        # would be an OOM kill of an unattended run rather than an error the
        # model can read. A stated budget turns it into the latter.
        con.execute(f"SET memory_limit = '{memory_limit}'")
        con.execute(f"SET threads = {threads}")
        # Otherwise an unknown table name resolves against Python locals in the
        # calling frame: `SELECT * FROM self` answers with the Database object
        # and the absolute path of the file it lives in, which then travels to
        # the model, to the inference provider, and into logs/runs.jsonl.
        con.execute("SET python_enable_replacements = false")
        con.execute("SET disabled_filesystems = 'LocalFileSystem'")
        con.execute("SET lock_configuration = true")
        return cls(con, lo, hi)

    def sql(self, query: str):
        return self.connection.sql(query)

    @property
    def tables(self) -> list[str]:
        return [r[0] for r in self.connection.sql("SHOW TABLES").fetchall()]
