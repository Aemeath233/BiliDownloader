from __future__ import annotations

import queue
from pathlib import Path

from bili_client.services.bili_api import BilibiliApi
from bili_client.services.auth_service import AuthService
from bili_client.services.config_store import ConfigStore
from bili_client.services.download_manager import DownloadManager
from bili_client.services.ffmpeg import FFmpegService
from bili_client.services.http_client import HttpClient
from bili_client.services.log_store import LogStore
from bili_client.services.single_instance import SingleInstance
from bili_client.services.task_store import TaskStore
from bili_client.ui.main_window import MainWindow


def main() -> None:
    single_instance = SingleInstance()
    if not single_instance.acquire():
        if single_instance.notify_existing():
            return
        single_instance = None

    log_queue: "queue.Queue[str]" = queue.Queue()

    config_store = ConfigStore()
    log_store = LogStore(config_store.logs_dir)

    def emit_log(message: str) -> None:
        log_queue.put(log_store.emit(message))

    config = config_store.load()

    http_client = HttpClient()
    auth_service = AuthService(
        http_client=http_client,
        auth_file=config_store.auth_file,
        logger=emit_log,
        check_interval_seconds=30 * 60,
    )
    auth_session = auth_service.load_session()
    if auth_session is not None and auth_session.sessdata:
        config.sessdata = auth_session.sessdata
        config_store.save(config)

    api = BilibiliApi(http_client=http_client, logger=emit_log)
    ffmpeg = FFmpegService(config.ffmpeg_path)
    task_store = TaskStore(config_store.tasks_file)
    manager = DownloadManager(api=api, ffmpeg=ffmpeg, config=config, logger=emit_log, task_store=task_store)

    icon_path = Path(__file__).resolve().parent.parent / "logo.ico"

    app = MainWindow(
        config_store=config_store,
        manager=manager,
        api=api,
        ffmpeg=ffmpeg,
        initial_config=config,
        log_queue=log_queue,
        emit_log=emit_log,
        log_file_path=log_store.current_log_file,
        log_dir_path=log_store.logs_dir,
        auth_service=auth_service,
        icon_path=icon_path if icon_path.exists() else None,
    )
    if single_instance is not None:
        single_instance.set_activate_callback(lambda: app.after(0, app.bring_to_front))
    try:
        app.mainloop()
    finally:
        if single_instance is not None:
            single_instance.close()
        manager.shutdown()
        http_client.close()
