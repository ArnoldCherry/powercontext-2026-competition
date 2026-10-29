from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _integer(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} 必须是整数，当前为 {value!r}") from error


@dataclass
class Settings:
    db_path: Path
    powercontext_mode: str
    powercontext_url: str
    powercontext_port: int
    powercontext_data_dir: Path
    powercontext_token: str
    scope_title: str
    scope_summary: str
    request_timeout: float
    auto_seed: bool
    board_host: str
    board_port: int


def load_settings() -> Settings:
    return Settings(
        db_path=Path(os.environ.get("BOARD_DB", ROOT / "data" / "board.db")).expanduser().resolve(),
        powercontext_mode=os.environ.get("POWERCONTEXT_MODE", "managed").strip().lower() or "managed",
        powercontext_url=os.environ.get("POWERCONTEXT_URL", "").strip().rstrip("/"),
        powercontext_port=_integer("POWERCONTEXT_PORT", 0),
        powercontext_data_dir=Path(
            os.environ.get("POWERCONTEXT_DATA_DIR", ROOT / "data" / "powercontext")
        ).expanduser().resolve(),
        powercontext_token=os.environ.get("POWERCONTEXT_TOKEN", ""),
        scope_title=os.environ.get("BOARD_SCOPE_TITLE", "生态赛交付看板"),
        scope_summary=os.environ.get(
            "BOARD_SCOPE_SUMMARY",
            "2026 OceanBase 生态赛的任务进度、提交证据与交接记忆。",
        ),
        request_timeout=float(os.environ.get("BOARD_REQUEST_TIMEOUT", "8")),
        auto_seed=_flag("BOARD_AUTO_SEED", True),
        board_host=os.environ.get("BOARD_HOST", "127.0.0.1"),
        board_port=_integer("BOARD_PORT", 18800),
    )
