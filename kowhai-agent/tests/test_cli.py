"""The unattended path: what survives a failure, and where it is allowed to write."""
import pytest
import typer

from kowhai_agent.cli import _draft_path


def test_a_draft_path_stays_inside_the_out_directory(tmp_path):
    assert _draft_path(tmp_path, "uoa03521") == tmp_path / "uoa03521.md"


@pytest.mark.parametrize("code", ["../escaped", "/etc/passwd", "a/b", "..", ""])
def test_an_account_code_cannot_steer_the_write_out_of_the_directory(tmp_path, code):
    """Codes come from the data -- whatever a converted sacct export contains."""
    with pytest.raises(typer.BadParameter):
        _draft_path(tmp_path, code)
    assert not (tmp_path.parent / "escaped.md").exists()


def test_one_failing_account_does_not_lose_the_drafts_already_written(tmp_path, make_agent):
    """advisory spends real money per account. Account 17 raising must not
    discard the 16 drafts that already succeeded."""
    from kowhai_agent.cli import _draft_each

    class Boom:
        def ask(self, question):
            if "bad" in question:
                raise RuntimeError("provider returned 500")
            return make_agent([("A note.", None)]).ask(question)

    summary = _draft_each(Boom(), ["good1", "bad", "good2"], tmp_path, "{account}")
    assert (tmp_path / "good1.md").exists() and (tmp_path / "good2.md").exists()
    assert summary.failed == ["bad"]
    assert "provider returned 500" in (tmp_path / "bad.md").read_text()
