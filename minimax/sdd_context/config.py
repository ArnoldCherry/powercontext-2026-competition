from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    seekdb_host: str
    seekdb_port: int
    seekdb_user: str
    seekdb_password: str
    seekdb_database: str
    powercontext_url: str
    powercontext_token: str
    minimax_api_key: str
    minimax_base_url: str
    minimax_model: str
    minimax_group_id: str
    project_root: Path
    runtime_dir: Path
    evidence_dir: Path


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def load_settings(
    project_root: Path | None = None,
    env_file: Path | None = None,
    runtime_dir: Path | None = None,
    evidence_dir: Path | None = None,
) -> Settings:
    if env_file is not None:
        load_dotenv(env_file)
    else:
        load_dotenv(PACKAGE_ROOT / ".env")

    project = (project_root or PACKAGE_ROOT / "sample_ledger").resolve()
    return Settings(
        seekdb_host=_env("SEEKDB_HOST", "127.0.0.1"),
        seekdb_port=int(_env("SEEKDB_PORT", "2881")),
        seekdb_user=_env("SEEKDB_USER", "root"),
        seekdb_password=_env("SEEKDB_PASSWORD"),
        seekdb_database=_env("SEEKDB_DATABASE", "sdd_context"),
        powercontext_url=_env("POWERCONTEXT_URL", "http://127.0.0.1:17429").rstrip("/"),
        powercontext_token=_env("POWERCONTEXT_TOKEN"),
        minimax_api_key=_env("MINIMAX_API_KEY"),
        minimax_base_url=_env("MINIMAX_BASE_URL", "https://api.minimax.chat/v1").rstrip("/"),
        minimax_model=_env("MINIMAX_MODEL", "MiniMax-M2"),
        minimax_group_id=_env("MINIMAX_GROUP_ID"),
        project_root=project,
        runtime_dir=(runtime_dir or PACKAGE_ROOT / "runtime").resolve(),
        evidence_dir=(evidence_dir or PACKAGE_ROOT / "evidence").resolve(),
    )
