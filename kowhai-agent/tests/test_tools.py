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


# --- Bypasses of the guardrails above. Each one worked before the guard was
# --- rewritten: the prefix check only ever inspected the start of the string.

def test_a_write_after_a_semicolon_is_rejected(toolbox, db):
    result = toolbox.call("run_sql", {"sql": (
        "SELECT 1 AS a FROM jobs WHERE submit_ts > TIMESTAMP '2000-01-01'; "
        "CREATE TABLE pwned AS SELECT 42;")}).result
    assert result.startswith("Error")
    assert "pwned" not in db.tables


def test_copy_to_a_file_after_a_semicolon_is_rejected(toolbox, tmp_path):
    exfil = tmp_path / "exfil.csv"
    result = toolbox.call("run_sql", {"sql": (
        f"SELECT 1 FROM jobs WHERE submit_ts > TIMESTAMP '2000-01-01'; "
        f"COPY (SELECT 1 AS leaked) TO '{exfil}';")}).result
    assert result.startswith("Error")
    assert not exfil.exists()


def test_a_time_filter_inside_a_comment_does_not_satisfy_the_guard(toolbox):
    result = toolbox.call("run_sql", {
        "sql": "SELECT COUNT(*) AS n FROM jobs -- submit_ts > 1"}).result
    assert result.startswith("Error: every query")


def test_a_time_column_inside_a_string_literal_does_not_satisfy_the_guard(toolbox):
    result = toolbox.call("run_sql", {
        "sql": "SELECT COUNT(*) AS n FROM jobs WHERE job_name = 'submit_ts > x'"}).result
    assert result.startswith("Error: every query")


def test_a_local_file_cannot_be_read_through_a_table_function(toolbox, tmp_path):
    secret = tmp_path / "secret.csv"
    secret.write_text("k,v\napikey,hunter2\n")
    result = toolbox.call("run_sql", {"sql": (
        f"SELECT t.content FROM read_text('{secret}') t, jobs j "
        f"WHERE j.submit_ts > TIMESTAMP '2000-01-01'")}).result
    assert result.startswith("Error")
    assert "hunter2" not in result


def test_the_filesystem_cannot_be_listed_through_glob(toolbox):
    result = toolbox.call("run_sql", {"sql": (
        "SELECT g.file FROM glob('/*') g, jobs j "
        "WHERE j.submit_ts > TIMESTAMP '2000-01-01'")}).result
    assert result.startswith("Error")


def test_a_common_table_expression_is_still_one_allowed_statement(toolbox):
    result = toolbox.call("run_sql", {"sql": (
        "WITH recent AS (SELECT job_id FROM jobs "
        "WHERE submit_ts > TIMESTAMP '2000-01-01') SELECT COUNT(*) AS n FROM recent")}).result
    assert not result.startswith("Error")


def test_unparseable_sql_is_returned_as_an_error_not_raised(toolbox):
    result = toolbox.call("run_sql", {
        "sql": "SELECT FROM WHERE submit_ts >"}).result
    assert result.startswith("Error")
