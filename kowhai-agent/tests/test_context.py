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
