import json
import types
from pathlib import Path

import pandas as pd
import pytest

from kowhai_agent import Agent, Database, build_toolbox

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def db(tmp_path_factory) -> Database:
    """A three-job dataset, enough to exercise every tool."""
    data = tmp_path_factory.mktemp("data")
    jobs = pd.DataFrame({
        "job_id": [1, 2, 3],
        "user": ["arangi", "bpatel", "arangi"],
        "account": ["uoa03521", "vuw03102", "uoa03521"],
        "project_name": ["Te Whare Wānanga o Tāmaki Makaurau — Molecular Dynamics"] * 3,
        "institution": ["University of Auckland"] * 3,
        "partition": ["large", "large", "gpu"],
        "state": ["COMPLETED", "TIMEOUT", "COMPLETED"],
        "last_reason": ["Priority", None, "Resources"],
        "job_name": ["gromacs_prod", "analysis.R", "train_asr"],
        "submit_ts": pd.to_datetime(["2026-07-06 01:00", "2026-07-06 02:00", "2026-07-06 03:00"]),
        "eligible_ts": pd.to_datetime(["2026-07-06 01:00", "2026-07-06 02:00", "2026-07-06 03:00"]),
        "start_ts": pd.to_datetime(["2026-07-06 01:10", "2026-07-06 02:30", "2026-07-06 03:05"]),
        "end_ts": pd.to_datetime(["2026-07-06 05:10", "2026-07-06 06:30", "2026-07-06 04:05"]),
        "req_cpus": [128, 128, 8],
        "elapsed_min": [240, 240, 60],
        "total_cpu_min": [28000.0, 250.0, 60.0],
        "planned_min": [10.0, 30.0, 5.0],
    })
    jobs.to_parquet(data / "jobs.parquet", index=False)
    return Database.open(data)


@pytest.fixture(scope="session")
def toolbox(db):
    inventory = json.loads((ROOT / "context" / "partitions.json").read_text())
    return build_toolbox(db, inventory, max_rows=2)


class FakeClient:
    """Replays a scripted sequence of model replies. No network, no cost."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # snapshot: the agent mutates its messages list in place, so storing the
        # reference would make every recorded call look like the last one
        self.seen.append({**kwargs, "messages": list(kwargs["messages"])})
        content, calls = self.script.pop(0)
        tool_calls = None
        if calls:
            tool_calls = [
                types.SimpleNamespace(
                    id=f"call_{i}",
                    function=types.SimpleNamespace(name=name, arguments=json.dumps(args)))
                for i, (name, args) in enumerate(calls)
            ]
        message = types.SimpleNamespace(content=content, tool_calls=tool_calls, role="assistant")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


@pytest.fixture
def make_agent(toolbox):
    def factory(script, **kwargs):
        return Agent(client=FakeClient(script), model="test-model",
                     system_prompt="You are a test.", toolbox=toolbox, **kwargs)
    return factory
