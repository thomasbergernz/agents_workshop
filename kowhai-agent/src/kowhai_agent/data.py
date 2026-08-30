"""DuckDB over the Parquet exports. Read-only by construction."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb


@dataclass
class Database:
    connection: duckdb.DuckDBPyConnection
    window_start: datetime
    window_end: datetime

    @classmethod
    def open(cls, data_dir: Path) -> Database:
        jobs = data_dir / "jobs.parquet"
        sched = data_dir / "sched_15m.parquet"
        if not jobs.exists():
            raise SystemExit(
                f"No jobs.parquet in {data_dir}. Generate the workshop dataset, or "
                "convert a real sacct export with sacct_to_parquet.py."
            )
        con = duckdb.connect(":memory:")
        con.execute("SET enable_progress_bar = false")
        con.execute(f"CREATE VIEW jobs AS SELECT * FROM '{jobs}'")
        if sched.exists():
            con.execute(f"CREATE VIEW sched_15m AS SELECT * FROM '{sched}'")
        lo, hi = con.sql("SELECT MIN(submit_ts), MAX(end_ts) FROM jobs").fetchone()
        return cls(con, lo, hi)

    def sql(self, query: str):
        return self.connection.sql(query)

    @property
    def tables(self) -> list[str]:
        return [r[0] for r in self.connection.sql("SHOW TABLES").fetchall()]
