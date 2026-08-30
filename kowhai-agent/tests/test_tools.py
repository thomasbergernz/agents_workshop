"""Guardrails, exercised without a model. The workshop's _selfcheck, as tests."""


def test_read_only(toolbox):
    """DROP TABLE, not DROP VIEW: since the Parquet is materialised, DROP VIEW
    is a harmless catalog error, so rejecting it proved nothing."""
    assert toolbox.call("run_sql", {"sql": "DROP TABLE jobs"}).result.startswith(
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


# --- The blanking pass must cover every way DuckDB spells a literal or an
# --- identifier, not just the single-quoted form. Each of these satisfied the
# --- time-filter guard while scanning the whole table.

def test_a_dollar_quoted_literal_does_not_satisfy_the_guard(toolbox):
    result = toolbox.call("run_sql", {
        "sql": "SELECT COUNT(*) AS n FROM jobs WHERE job_name = $$submit_ts > x$$"}).result
    assert result.startswith("Error: every query")


def test_a_tagged_dollar_quoted_literal_does_not_satisfy_the_guard(toolbox):
    result = toolbox.call("run_sql", {
        "sql": "SELECT COUNT(*) AS n FROM jobs WHERE job_name = $q$submit_ts > x$q$"}).result
    assert result.startswith("Error: every query")


def test_a_time_column_inside_a_quoted_identifier_does_not_satisfy_the_guard(toolbox):
    result = toolbox.call("run_sql", {
        "sql": 'SELECT COUNT(*) AS "submit_ts > x" FROM jobs'}).result
    assert result.startswith("Error: every query")


def test_a_real_predicate_still_passes_alongside_those_forms(toolbox):
    """The blanking must not eat the predicate itself."""
    result = toolbox.call("run_sql", {
        "sql": "SELECT COUNT(*) AS n FROM jobs "
               "WHERE submit_ts > TIMESTAMP '2000-01-01' AND job_name = $$x$$"}).result
    assert not result.startswith("Error")


def test_the_row_cap_is_applied_by_the_database_not_by_pandas(toolbox):
    """Six million rows must not be built in pandas so that fifty can be shown.

    Both paths return the same text, so the only observable difference is the
    work done: materialising the full result takes seconds, capping it in the
    query takes milliseconds. The bound is ~100x looser than the measured gap.
    """
    call = toolbox.call("run_sql", {
        "sql": "SELECT j.job_name FROM jobs j, range(1, 2000000) "
               "WHERE j.submit_ts > TIMESTAMP '2000-01-01'"})
    assert "truncated at" in call.result
    assert call.seconds < 0.5, f"took {call.seconds:.2f}s — the full result was materialised"


def test_an_underscore_in_a_lookup_fragment_is_not_a_wildcard(toolbox):
    """`_` must mean a literal underscore, not "any character"."""
    values = toolbox.call("list_values",
                          {"column": "job_name", "contains": "_"}).result.split("\n")
    assert "gromacs_prod" in values and "train_asr" in values   # real underscores
    assert "analysis.R" not in values                            # matched only as a wildcard


def test_a_percent_in_a_lookup_fragment_is_not_a_wildcard(toolbox):
    assert toolbox.call("list_values", {"column": "job_name", "contains": "%"}) \
        .result.startswith("No job_name value")


def test_a_single_enormous_row_is_capped_by_bytes_not_just_rows(toolbox):
    """One row can flood the context window without ever reaching the row cap,
    and run_sql's own docstring tells the model to aggregate."""
    result = toolbox.call("run_sql", {
        "sql": "SELECT string_agg(repeat(job_name, 5000), ', ') AS everything "
               "FROM jobs WHERE submit_ts > TIMESTAMP '2000-01-01'"}).result
    assert len(result) < 50_000, f"returned {len(result):,} characters to the model"
