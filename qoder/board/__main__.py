from __future__ import annotations

import os
import socket
import sys

import uvicorn

from board.config import load_settings


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def main() -> int:
    settings = load_settings()
    if not _port_available(settings.board_host, settings.board_port):
        print(
            f"看板端口 {settings.board_port} 已被占用。"
            "请设置 BOARD_PORT 为其他空闲端口后重试。",
            file=sys.stderr,
        )
        return 2
    print(f"看板启动中：http://{settings.board_host}:{settings.board_port}")
    print(
        f"PowerContext 模式：{settings.powercontext_mode}；"
        "managed 模式会自动启动真实 PowerContext Server 并选择空闲端口。"
    )
    uvicorn.run(
        "board.app:app",
        host=settings.board_host,
        port=settings.board_port,
        reload=False,
        log_level=os.environ.get("BOARD_LOG_LEVEL", "info"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
