"""Settings, all overridable by environment variable."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent


def _context_dir() -> Path:
    """Where the schema cards live.

    In a checkout that is <repo>/context. From a wheel, PROJECT_ROOT resolves
    into site-packages and there is no context/ beside it, so the copy shipped
    inside the package is used instead -- otherwise the installed `kowhai`
    command exits at startup on a directory that was never packaged.
    """
    checkout = PROJECT_ROOT / "context"
    return checkout if checkout.is_dir() else PACKAGE_ROOT / "context"


@dataclass(frozen=True)
class Settings:
    """Every field is overridable by environment variable -- via from_env().

    These were read in the class body, which runs once when the module is first
    imported: setting KOWHAI_DATA_DIR afterwards did nothing, monkeypatch could
    not reach them, and a typo in KOWHAI_MAX_ROWS raised ValueError during
    `import kowhai_agent`, killing even the commands that need no API key.
    """

    model: str = "google/gemini-3.5-flash-lite"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key: str = ""
    data_dir: Path = PROJECT_ROOT / "data"
    context_dir: Path = field(default_factory=_context_dir)
    log_path: Path = PROJECT_ROOT / "logs" / "runs.jsonl"
    max_rows: int = 50

    @classmethod
    def from_env(cls) -> Settings:
        defaults = cls()
        raw = os.environ.get("KOWHAI_MAX_ROWS", str(defaults.max_rows))
        try:
            max_rows = int(raw)
        except ValueError:
            raise SystemExit(f"KOWHAI_MAX_ROWS must be a whole number, not {raw!r}.") from None
        return cls(
            model=os.environ.get("KOWHAI_MODEL", defaults.model),
            base_url=os.environ.get("KOWHAI_BASE_URL", defaults.base_url),
            api_key=os.environ.get("OPENROUTER_API_KEY", defaults.api_key),
            data_dir=Path(os.environ.get("KOWHAI_DATA_DIR", defaults.data_dir)),
            context_dir=Path(os.environ.get("KOWHAI_CONTEXT_DIR", defaults.context_dir)),
            log_path=Path(os.environ.get("KOWHAI_LOG", defaults.log_path)),
            max_rows=max_rows,
        )

    def require_api_key(self) -> str:
        if not self.api_key:
            raise SystemExit(
                "No API key. Set OPENROUTER_API_KEY, or use a command that does "
                "not call a model (try: kowhai selfcheck)."
            )
        return self.api_key


settings = Settings.from_env()
