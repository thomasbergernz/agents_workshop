"""Settings, all overridable by environment variable."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent


@dataclass(frozen=True)
class Settings:
    model: str = os.environ.get("KOWHAI_MODEL", "google/gemini-3.5-flash-lite")
    base_url: str = os.environ.get("KOWHAI_BASE_URL", "https://openrouter.ai/api/v1")
    api_key: str = os.environ.get("OPENROUTER_API_KEY", "")
    data_dir: Path = Path(os.environ.get("KOWHAI_DATA_DIR", PROJECT_ROOT / "data"))
    context_dir: Path = Path(os.environ.get("KOWHAI_CONTEXT_DIR", PROJECT_ROOT / "context"))
    log_path: Path = Path(os.environ.get("KOWHAI_LOG", PROJECT_ROOT / "logs" / "runs.jsonl"))
    max_rows: int = int(os.environ.get("KOWHAI_MAX_ROWS", "50"))

    def require_api_key(self) -> str:
        if not self.api_key:
            raise SystemExit(
                "No API key. Set OPENROUTER_API_KEY, or use a command that does "
                "not call a model (try: kowhai selfcheck)."
            )
        return self.api_key


settings = Settings()
