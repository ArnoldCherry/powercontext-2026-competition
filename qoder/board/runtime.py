from __future__ import annotations

import importlib.util
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from board.config import ROOT, Settings


class PowerContextRuntimeError(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _ensure_powercontext_package() -> None:
    if importlib.util.find_spec("powercontext") is None:
        raise PowerContextRuntimeError(
            "当前 Python 环境未安装 powercontext。请先执行 "
            "`python -m pip install -r requirements.txt`（包含 powercontext[cli,server]）。"
        )


def _powercontext_command(port: int) -> list[str]:
    # Invoke the CLI in this interpreter instead of the console-script shim:
    # on Windows the .exe shim spawns a child python process that terminate() cannot stop.
    return [
        sys.executable,
        "-c",
        "from powercontext.cli.app import main; main()",
        "server",
        "run",
        "--no-env-file",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


def _read_log_tail(path: Path, limit: int = 3000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:].strip()


class PowerContextRuntime:
    """Starts and owns the real PowerContext Server for the lifetime of the board."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.mode = settings.powercontext_mode
        self.url = ""
        self.pid: int | None = None
        self.data_dir = settings.powercontext_data_dir
        self.log_path = self.data_dir.parent / "powercontext-managed.log"
        self.error: str | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle = None

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "url": self.url,
            "managed": self._process is not None and self._process.poll() is None,
            "pid": self.pid,
            "data_dir": str(self.data_dir),
            "log_path": str(self.log_path),
            "error": self.error,
        }

    def start(self) -> None:
        if self.mode not in {"managed", "external"}:
            raise PowerContextRuntimeError(
                f"POWERCONTEXT_MODE={self.mode!r} 无效；可选 managed（默认）或 external。"
            )
        if self.mode == "external":
            self._start_external()
        else:
            self._start_managed()

    def _start_external(self) -> None:
        if not self.settings.powercontext_url:
            raise PowerContextRuntimeError(
                "POWERCONTEXT_MODE=external 时必须设置 POWERCONTEXT_URL，例如 http://127.0.0.1:18801。"
            )
        self.url = self.settings.powercontext_url
        self._wait_external()
        self.settings.powercontext_url = self.url

    def _start_managed(self) -> None:
        _ensure_powercontext_package()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        requested = self.settings.powercontext_port
        if requested and not _port_available(requested):
            raise PowerContextRuntimeError(
                f"PowerContext 端口 {requested} 已被占用。"
                "请设置 POWERCONTEXT_PORT=0 自动选择空闲端口，或释放该端口。"
            )
        port = requested or _free_port()
        env = os.environ.copy()
        for key in [key for key in env if key.startswith("POWERCONTEXT_SERVER_")]:
            env.pop(key, None)
        env.update(
            {
                "POWERCONTEXT_HOME": str(self.data_dir),
                "POWERCONTEXT_SERVER_DATABASE_KIND": "sqlite",
                "POWERCONTEXT_SERVER_ACCESS_MODE": "disabled",
                "POWERCONTEXT_SERVER_MCP_ENABLED": "false",
                "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "false",
                "POWERCONTEXT_SERVER_EXTERNAL_SKILLS": "{}",
                "PYTHONUNBUFFERED": "1",
            }
        )
        command = _powercontext_command(port)
        self._log_handle = self.log_path.open("w", encoding="utf-8")
        self._log_handle.write("starting managed PowerContext: " + " ".join(command) + "\n")
        self._log_handle.flush()
        kwargs: dict[str, Any] = {
            "cwd": str(ROOT),
            "env": env,
            "stdout": self._log_handle,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        self._process = subprocess.Popen(command, **kwargs)
        self.pid = self._process.pid
        self.url = f"http://127.0.0.1:{port}"
        try:
            self._wait_http_ready(timeout=45.0, process=self._process)
        except PowerContextRuntimeError:
            self.stop()
            raise
        self.settings.powercontext_url = self.url

    def _wait_external(self) -> None:
        try:
            response = httpx.get(f"{self.url}/health/ready", timeout=3.0, trust_env=False)
        except httpx.HTTPError as error:
            raise PowerContextRuntimeError(
                f"外部 PowerContext 不可达：{self.url}（{error}）。"
                "请启动服务，或使用默认 POWERCONTEXT_MODE=managed 让应用自动启动。"
            ) from error
        if response.status_code != 200:
            raise PowerContextRuntimeError(
                f"外部 PowerContext 未就绪：{self.url} 返回 HTTP {response.status_code}。"
            )

    def _wait_http_ready(self, timeout: float, process: subprocess.Popen[bytes]) -> None:
        deadline = time.monotonic() + timeout
        last_error = ""
        while time.monotonic() < deadline:
            if process.poll() is not None:
                tail = _read_log_tail(self.log_path)
                raise PowerContextRuntimeError(
                    f"PowerContext 进程提前退出（exit={process.returncode}）。"
                    f"日志：{self.log_path}\n{tail}"
                )
            try:
                response = httpx.get(f"{self.url}/health/ready", timeout=1.5, trust_env=False)
                if response.status_code == 200 and response.json().get("status") == "ready":
                    return
                last_error = f"HTTP {response.status_code}"
            except (httpx.HTTPError, ValueError) as error:
                last_error = str(error)
            time.sleep(0.25)
        tail = _read_log_tail(self.log_path)
        raise PowerContextRuntimeError(
            f"等待 PowerContext 就绪超时（{timeout:.0f}s，{last_error}）。"
            f"日志：{self.log_path}\n{tail}"
        )

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            if os.name == "nt":
                # Windows venv python can be a redirector; /T is required to stop the real server child.
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        self.pid = None
