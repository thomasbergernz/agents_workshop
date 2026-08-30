"""The loop: one pass, tool chaining, error recovery, accounting, logging."""
import json


def test_answers_without_tools(make_agent):
    agent = make_agent([("42 core-hours.", None)])
    run = agent.ask("How many?")
    assert run.answer == "42 core-hours." and run.calls == [] and run.model_calls == 1


def test_runs_a_tool_then_answers(make_agent):
    agent = make_agent([
        (None, [("partition_info", {"partition": "large"})]),
        ("It is 240 nodes.", None),
    ])
    run = agent.ask("How big is large?")
    assert run.answer == "It is 240 nodes."
    assert [c.name for c in run.calls] == ["partition_info"]
    assert run.model_calls == 2 and run.prompt_tokens_estimate > 0


def test_a_failed_tool_call_is_fed_back_and_can_be_corrected(make_agent):
    agent = make_agent([
        (None, [("run_sql", {"sql": "SELECT COUNT(*) FROM jobs"})]),          # no time filter
        (None, [("run_sql", {"sql": "SELECT COUNT(*) AS n FROM jobs "
                                    "WHERE submit_ts > TIMESTAMP '2000-01-01'"})]),
        ("Three jobs.", None),
    ])
    run = agent.ask("How many jobs?")
    assert run.failed_calls == 1 and run.answer == "Three jobs."


def test_history_is_resent_so_context_is_paid_for_repeatedly(make_agent):
    agent = make_agent([
        (None, [("partition_info", {"partition": "large"})]),
        (None, [("partition_info", {"partition": "gpu"})]),
        ("Done.", None),
    ])
    run = agent.ask("Compare them")
    sent = agent.client.seen
    assert len(sent[-1]["messages"]) > len(sent[0]["messages"])
    assert run.prompt_tokens_estimate > 0


def test_stops_after_max_iters_without_looping_forever(make_agent):
    agent = make_agent([(None, [("partition_info", {"partition": "large"})])] * 5,
                       max_iters=3)
    run = agent.ask("Loop please")
    assert run.stopped_early and run.answer is None and run.model_calls == 3


def test_run_is_logged_as_jsonl(make_agent, tmp_path):
    log = tmp_path / "runs.jsonl"
    agent = make_agent([(None, [("partition_info", {"partition": "large"})]),
                        ("Answer.", None)], log_path=log)
    agent.ask("Question?")
    record = json.loads(log.read_text().strip())
    assert record["model_calls"] == 2 and record["calls"][0]["name"] == "partition_info"
    assert "answer" not in record            # drafts are written separately


def test_malformed_tool_arguments_are_fed_back_not_raised(make_agent):
    """max_tokens can cut a completion mid-argument. Every other model mistake
    becomes a message the model can correct; this one killed the process."""
    agent = make_agent([
        (None, [("run_sql", '{"sql": "SELECT * FROM jobs WHE')]),      # truncated
        ("Recovered.", None),
    ])
    run = agent.ask("How many jobs?")
    assert run.failed_calls == 1
    assert run.answer == "Recovered."


def test_content_alongside_tool_calls_is_not_discarded(make_agent):
    """A reply carrying both prose and a tool call lost the prose entirely."""
    agent = make_agent([
        ("Partial thought.", [("partition_info", {"partition": "large"})]),
        ("Final answer.", None),
    ])
    run = agent.ask("How big is large?")
    assert "Partial thought." in (run.content_before_answer or "")
    assert run.answer == "Final answer."


def test_an_empty_reply_is_not_reported_as_running_out_of_iterations(make_agent):
    """content=None with no tool calls is a refusal or a filtered completion,
    not exhaustion -- the CLI printed the wrong diagnosis for it."""
    run = make_agent([(None, None)]).ask("q")
    assert run.answer is None and not run.stopped_early
    assert "iteration" not in run.no_answer_reason.lower()


def test_running_out_of_iterations_says_so(make_agent):
    run = make_agent([(None, [("partition_info", {"partition": "large"})])] * 4,
                     max_iters=3).ask("Loop please")
    assert run.stopped_early
    assert "iteration" in run.no_answer_reason.lower()
