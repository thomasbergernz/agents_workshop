"""The generated specs must match the functions they describe."""
import pytest

from kowhai_agent import tool


def test_spec_is_generated_from_signature_and_docstring():
    @tool
    def demo(name: str, count: int = 3) -> str:
        """Do a thing to a named subject.

        name: What to do it to.
        count: How many times.
        """
        return f"{name} x {count}"

    fn = demo.tool_spec["function"]
    assert fn["name"] == "demo"
    assert fn["description"] == "Do a thing to a named subject."
    assert fn["parameters"]["properties"]["name"] == {
        "type": "string", "description": "What to do it to."}
    assert fn["parameters"]["properties"]["count"]["type"] == "integer"
    assert fn["parameters"]["required"] == ["name"]        # count has a default


def test_literal_becomes_an_enum():
    from typing import Literal

    @tool
    def pick(colour: Literal["red", "blue"]) -> str:
        """Pick a colour.

        colour: Which one.
        """
        return colour

    assert pick.tool_spec["function"]["parameters"]["properties"]["colour"]["enum"] == \
        ["red", "blue"]


def test_unsupported_type_is_rejected_at_import_time():
    with pytest.raises(TypeError):
        @tool
        def bad(payload: dict) -> str:
            """Take a dict."""
            return ""


def test_missing_docstring_is_rejected():
    with pytest.raises(ValueError):
        @tool
        def undocumented(x: str) -> str:
            return x


def test_tool_errors_are_returned_not_raised(toolbox):
    call = toolbox.call("run_sql", {"sql": "SELECT * FROM nonexistent WHERE ts > NOW()"})
    assert call.failed and call.result.startswith("Error")


def test_unknown_tool_is_reported_to_the_model(toolbox):
    call = toolbox.call("no_such_tool", {})
    assert call.failed and "no tool named" in call.result


def test_toolbox_replacement_keeps_the_others(toolbox):
    @tool
    def run_sql(sql: str) -> str:
        """Replacement.

        sql: A query.
        """
        return "replaced"

    swapped = toolbox.with_(run_sql)
    assert swapped.call("run_sql", {"sql": "x"}).result == "replaced"
    assert set(swapped) == set(toolbox)


def test_a_result_that_merely_starts_with_error_is_not_a_failed_call(toolbox):
    """`failed` was a substring test on English prose. It is the operator's only
    per-account triage signal, and job names beginning with 'error' are legal."""
    @tool
    def echo(x: str) -> str:
        """Echo a value.

        x: Anything.
        """
        return "error_handler.log"

    assert not toolbox.with_(echo).call("echo", {"x": "q"}).failed


def test_a_tool_that_reports_it_could_not_answer_is_a_failed_call(toolbox):
    assert toolbox.call("partition_info", {"partition": "nope"}).failed
    assert toolbox.call("list_values", {"column": "account", "contains": "zzzz"}).failed


def test_the_lookup_allow_list_cannot_drift_from_the_advertised_enum(toolbox):
    from typing import get_args

    from kowhai_agent.tools import LOOKUP_COLUMNS, LookupColumn
    assert set(get_args(LookupColumn)) == set(LOOKUP_COLUMNS)
