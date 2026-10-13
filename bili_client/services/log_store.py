from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from bili_client.core.security import redact_sensitive


class LogStore:
    def __init__(self, logs_dir: Path) -> None:
        self.logs_dir = logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.current_log_file = self.logs_dir / f"bili-downloader-{datetime.now():%Y%m%d}.log"
        self._lock = threading.Lock()
        self._append_line("日志会话开始")

    def emit(self, message: str) -> str:
        message = redact_sensitive(message)
        ui_line = f"[{datetime.now():%H:%M:%S}] {message}"
        self._append_line(message)
        return ui_line

    def _append_line(self, message: str) -> None:
        message = redact_sensitive(message)
        file_line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n"
        with self._lock:
            self.current_log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.current_log_file.open("a", encoding="utf-8") as file_handle:
                file_handle.write(file_line)
