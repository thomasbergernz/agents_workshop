"""The connection the model queries through, and the limits it cannot lift."""
import duckdb
import pytest


def setting(db, name: str) -> str:
    return db.connection.execute(f"SELECT current_setting('{name}')").fetchone()[0]


def test_the_connection_has_a_memory_limit(db):
    """With no filesystem there is no spill, so an unbounded query is an OOM
    kill of the process rather than an error the model can read and correct."""
    assert setting(db, "memory_limit") != "0 bytes"
    limit_gib = float(setting(db, "memory_limit").split()[0].replace(",", ""))
    assert limit_gib <= 4, f"memory_limit is {setting(db, 'memory_limit')} — the host default"


def test_the_limits_cannot_be_lifted_once_open(db):
    for statement in ("SET memory_limit = '100GB'",
                      "SET disabled_filesystems = ''",
                      "SET threads = 64"):
        with pytest.raises(duckdb.InvalidInputException):
            db.connection.execute(statement)


def test_model_authored_sql_cannot_reach_python_objects(db):
    """DuckDB's replacement scan resolves an unknown table name against locals
    in the calling frame, and says so — naming the file — in the error the
    model reads."""
    with pytest.raises(duckdb.Error) as caught:
        db.sql("SELECT * FROM self")   # `self` is a local of Database.sql
    message = str(caught.value)
    assert "Python Object" not in message, message
    assert ".py" not in message, message
