"""Context is content: it must load, and it must say the things the tools rely on."""
from pathlib import Path

from kowhai_agent import load_context
from kowhai_agent.context import context_files

CONTEXT = Path(__file__).resolve().parent.parent / "context"


def test_files_load_in_filename_order():
    names = [name for name, _ in context_files(CONTEXT)]
    assert names == sorted(names)
    assert names[0].startswith("00-")


def test_prompt_carries_the_facts_the_answers_depend_on():
    prompt = load_context(CONTEXT)
    assert "UTC" in prompt and "NZST" in prompt          # the Part 6 correction
    assert "planned_min" in prompt                        # the Part 7 correction
    assert "Never average an efficiency ratio" in prompt  # the Part 12 silent error


def _cards(tmp_path):
    (tmp_path / "00-role.md").write_text("You are an analyst.\n")
    (tmp_path / "10-jobs.md").write_text("## Table: jobs\nOne row per allocation.\n")
    (tmp_path / "20-sched-15m.md").write_text("## Table: sched_15m\nOne row per sample.\n")


def test_the_schema_card_for_an_absent_table_is_not_sent_to_the_model(tmp_path):
    """sched_15m.parquet is optional, but its card went into the prompt anyway,
    so the model spent iterations querying a table that was never loaded."""
    _cards(tmp_path)
    prompt = load_context(tmp_path, tables=["jobs"])
    assert "One row per allocation." in prompt
    assert "sched_15m" not in prompt
    assert "You are an analyst." in prompt      # a card-less file is always kept


def test_every_card_is_sent_when_every_table_is_loaded(tmp_path):
    _cards(tmp_path)
    prompt = load_context(tmp_path, tables=["jobs", "sched_15m"])
    assert "One row per sample." in prompt
