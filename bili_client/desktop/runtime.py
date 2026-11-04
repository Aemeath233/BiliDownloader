from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, replace

from bili_client.core.models import AppConfig
from bili_client.core.security import redact_sensitive


class DesktopRuntime:
    """Own service lifetime and snapshots without depending on a GUI event loop."""

    def __init__(self, config_store=None) -> None:
        self.lock = threading.RLock()
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.startup_error = ""
        self.config = AppConfig()
        self.config_store = config_store
        self.manager = self.api = self.ffmpeg = self.auth = self.http = self.log_store = None
        self.logs = deque(maxlen=5000)
        self.log_sequence = 0
        self.auth_version = 0

    def initialize(self) -> None:
        from bili_client.services.auth_service import AuthService, AuthSession
        from bili_client.services.bili_api import BilibiliApi
        from bili_client.services.config_store import ConfigStore
        from bili_client.services.download_manager import DownloadManager
        from bili_client.services.ffmpeg import FFmpegService
        from bili_client.services.http_client import HttpClient
        from bili_client.services.log_store import LogStore
        from bili_client.services.task_store import TaskStore

        started = time.perf_counter()
        try:
            self.config_store = self.config_store or ConfigStore()
            self.log_store = LogStore(self.config_store.logs_dir)
            config = self.config_store.load()
            legacy = bool(config.sessdata)
            self.http = HttpClient(steady_mode=config.steady_mode)
            self.auth = AuthService(self.http, self.config_store.auth_file, self.emit, check_interval_seconds=1800)
            session = self.auth.load_session()
            if session is None and config.sessdata:
                session = AuthSession(cookies={"SESSDATA": config.sessdata})
                self.auth.save_session(session)
            if session and session.sessdata:
                config.sessdata = session.sessdata
            if legacy:
                self.config_store.save(config)
                self.config_store.remove_loaded_legacy_sessdata_file()
            self.config = config
            self.api = BilibiliApi(self.http, self.emit)
            self.ffmpeg = FFmpegService(config.ffmpeg_path)
            self.manager = DownloadManager(
                self.api, self.ffmpeg, config, self.emit, self.refresh_auth, TaskStore(self.config_store.tasks_file)
            )
            if self.closed.is_set():
                self.manager.shutdown()
                self.http.close()
                return
            self.ready.set()
            self.emit(f"WebView 下载服务就绪 {time.perf_counter() - started:.2f}s")
        except Exception as exc:
            self.startup_error = redact_sensitive(str(exc))
            self.emit(f"启动失败：{self.startup_error}")
            if self.http is not None:
                self.http.close()

    def emit(self, message: str) -> None:
        line = redact_sensitive(message)
        if self.log_store is not None:
            try:
                line = self.log_store.emit(line)
            except OSError:
                line = f"[日志文件写入失败] {line}"
        with self.lock:
            self.log_sequence += 1
            self.logs.append({"id": self.log_sequence, "text": line})

    def public_config(self) -> dict:
        with self.lock:
            result = asdict(self.config)
        result.pop("sessdata")
        return result

    def refresh_auth(self, force: bool = False) -> str:
        with self.lock:
            fallback = self.config.sessdata
        try:
            session = self.auth.ensure_latest_session(fallback, force=force)
            with self.lock:
                # Ignore stale network results following sign-out or another login.
                current = self.auth.load_session()
                if session is not None and current is session and self.config.sessdata == fallback:
                    self.accept_account(session.sessdata)
                return self.config.sessdata
        except Exception as exc:
            self.emit(f"登录态检查失败：{exc}")
            with self.lock:
                return self.config.sessdata

    def accept_account(self, sessdata: str) -> None:
        with self.lock:
            if sessdata != self.config.sessdata:
                self.config = replace(self.config, sessdata=sessdata)
                self.auth_version += 1
                self.manager.update_config(self.config)

    def close(self) -> None:
        with self.lock:
            self.closed.set()
        try:
            if self.manager is not None:
                self.manager.shutdown()
        finally:
            if self.http is not None:
                self.http.close()
