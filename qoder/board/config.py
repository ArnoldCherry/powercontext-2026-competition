from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    db_path: Path
    powercontext_url: str
    powercontext_token: str
    scope_title: str
    scope_summary: str
    request_timeout: float


def load_settings() -> Settings:
    return Settings(
        db_path=Path(os.environ.get("BOARD_DB", ROOT / "data" / "board.db")).expanduser().resolve(),
        powercontext_url=os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:18801").rstrip("/"),
        powercontext_token=os.environ.get("POWERCONTEXT_TOKEN", ""),
        scope_title=os.environ.get("BOARD_SCOPE_TITLE", "生态赛交付看板"),
        scope_summary=os.environ.get(
            "BOARD_SCOPE_SUMMARY",
            "2026 OceanBase 生态赛的任务进度、提交证据与交接记忆。",
        ),
        request_timeout=float(os.environ.get("BOARD_REQUEST_TIMEOUT", "8")),
    )
