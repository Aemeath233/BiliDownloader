from __future__ import annotations

import queue
import time
from pathlib import Path


def main(started_at: float | None = None) -> None:
    started_at = started_at or time.perf_counter()
    from bili_client.services.single_instance import SingleInstance

    single_instance = SingleInstance()
    if not single_instance.acquire():
        if single_instance.notify_existing():
            return
        single_instance = None

    # Paint the shell before importing networking, loading certificates,
    # reading task history, or constructing secondary pages.
    from bili_client.ui.main_window import MainWindow

    icon_path = Path(__file__).resolve().parent.parent / "logo.ico"
    app = MainWindow(icon_path=icon_path if icon_path.exists() else None, started_at=started_at)
    resources: dict = {}

    def initialize_services() -> dict:
        from bili_client.services.auth_service import AuthService, AuthSession
        from bili_client.services.bili_api import BilibiliApi
        from bili_client.services.config_store import ConfigStore
        from bili_client.services.download_manager import DownloadManager
        from bili_client.services.ffmpeg import FFmpegService
        from bili_client.services.http_client import HttpClient
        from bili_client.services.log_store import LogStore
        from bili_client.services.task_store import TaskStore

        log_queue: queue.Queue[str] = queue.Queue()
        config_store = ConfigStore()
        log_store = LogStore(config_store.logs_dir)

        def emit_log(message: str) -> None:
            log_queue.put(log_store.emit(message))

        config = config_store.load()
        had_legacy_config_sessdata = bool(config.sessdata)
        http_client = HttpClient(steady_mode=config.steady_mode)
        resources["http_client"] = http_client
        try:
            auth_service = AuthService(
                http_client=http_client,
                auth_file=config_store.auth_file,
                logger=emit_log,
                check_interval_seconds=30 * 60,
            )
            auth_session = auth_service.load_session()
            if auth_session is None and config.sessdata:
                auth_session = AuthSession(cookies={"SESSDATA": config.sessdata})
                auth_service.save_session(auth_session)
            if auth_session is not None and auth_session.sessdata:
                config.sessdata = auth_session.sessdata
            if had_legacy_config_sessdata:
                config_store.save(config)
                if auth_session is not None and auth_session.sessdata:
                    config_store.remove_loaded_legacy_sessdata_file()
            api = BilibiliApi(http_client=http_client, logger=emit_log)
            ffmpeg = FFmpegService(config.ffmpeg_path)
            manager = DownloadManager(
                api=api, ffmpeg=ffmpeg, config=config, logger=emit_log, task_store=TaskStore(config_store.tasks_file)
            )
            resources["manager"] = manager
            if app._closing:
                manager.shutdown()
                http_client.close()
            return dict(
                config_store=config_store,
                manager=manager,
                api=api,
                ffmpeg=ffmpeg,
                current_config=config,
                log_queue=log_queue,
                emit_log=emit_log,
                log_file_path=log_store.current_log_file,
                log_dir_path=log_store.logs_dir,
                auth_service=auth_service,
            )
        except Exception:
            http_client.close()
            raise

    if single_instance is not None:
        single_instance.set_activate_callback(lambda: app.ui_tasks.post(app.bring_to_front))
    app.after(
        30,
        lambda: app.ui_tasks.run(
            "initialize-services",
            initialize_services,
            on_success=lambda services: app.initialize_services(**services),
            on_error=app.show_startup_error,
        ),
    )
    try:
        app.mainloop()
    finally:
        if single_instance is not None:
            single_instance.close()
        manager = resources.get("manager")
        if manager is not None:
            manager.shutdown()
        http_client = resources.get("http_client")
        if http_client is not None:
            http_client.close()
