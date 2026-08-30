"""Guardrails, exercised without a model. The workshop's _selfcheck, as tests."""


def test_read_only(toolbox):
    assert toolbox.call("run_sql", {"sql": "DROP VIEW jobs"}).result.startswith(
        "Error: only SELECT")


def test_time_filter_required_and_hint_quotes_the_real_window(toolbox, db):
    result = toolbox.call("run_sql", {"sql": "SELECT COUNT(*) FROM jobs"}).result
    assert result.startswith("Error: every query")
    assert f"{db.window_start:%Y-%m-%d}" in result


def test_row_cap_truncates(toolbox):
    result = toolbox.call("run_sql", {
        "sql": "SELECT job_id FROM jobs WHERE submit_ts > TIMESTAMP '2000-01-01'"}).result
    assert "truncated at 2 rows" in result


def test_empty_result_explains_itself(toolbox):
    result = toolbox.call("run_sql", {
        "sql": "SELECT * FROM jobs WHERE submit_ts > TIMESTAMP '2099-01-01'"}).result
    assert result.startswith("0 rows")


def test_list_values_finds_stored_spelling(toolbox):
    """A guessed spelling without the macron finds nothing; the real one does."""
    values = toolbox.call("list_values", {"column": "project_name", "contains": "Wānanga"}).result
    assert "Wānanga" in values
    assert toolbox.call("list_values", {"column": "project_name", "contains": "Wananga"}) \
        .result.startswith("No project_name value")


def test_list_values_rejects_columns_outside_the_allow_list(toolbox):
    assert toolbox.call("list_values", {"column": "req_cpus"}).result.startswith("Error")


def test_partition_info_reaches_outside_the_database(toolbox):
    result = toolbox.call("partition_info", {"partition": "large"}).result
    assert "charge_per_core_hour" in result and "core-hours available per day" in result
