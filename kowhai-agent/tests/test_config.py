"""Settings promise env overrides. They were frozen at import."""
import pytest

from kowhai_agent.config import Settings


def test_env_overrides_are_read_when_settings_are_built(monkeypatch):
    monkeypatch.setenv("KOWHAI_MAX_ROWS", "7")
    monkeypatch.setenv("KOWHAI_MODEL", "some/other-model")
    settings = Settings.from_env()
    assert settings.max_rows == 7
    assert settings.model == "some/other-model"


def test_a_non_numeric_row_cap_fails_with_a_sentence_not_a_traceback(monkeypatch):
    """It was read in the dataclass body, so a typo killed `import kowhai_agent`
    -- every command including the one that needs no API key."""
    monkeypatch.setenv("KOWHAI_MAX_ROWS", "fifty")
    with pytest.raises(SystemExit, match="KOWHAI_MAX_ROWS"):
        Settings.from_env()


def test_the_missing_key_message_names_a_command_that_works_without_one():
    with pytest.raises(SystemExit, match="selfcheck"):
        Settings(api_key="").require_api_key()
