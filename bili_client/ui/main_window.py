from __future__ import annotations

import os
import queue
import time
from dataclasses import replace
from pathlib import Path
from tkinter import messagebox
from typing import TYPE_CHECKING

import customtkinter as ctk

from bili_client.core.formatters import format_speed
from bili_client.core.models import AppConfig
from bili_client.ui.async_tasks import UiTaskRunner
from bili_client.ui.motion import motion_for
from bili_client.ui.styles import CARD_RADIUS, FONT_FAMILY, PALETTE, PANEL_RADIUS
from bili_client.ui.widgets import MotionButton, TextLabel, Toast

if TYPE_CHECKING:
    from bili_client.services.auth_service import AuthService
    from bili_client.services.bili_api import BilibiliApi
    from bili_client.services.config_store import ConfigStore
    from bili_client.services.download_manager import DownloadManager
    from bili_client.services.ffmpeg import FFmpegService


class MainWindow(ctk.CTk):
    POLL_INTERVAL_MS = 400
    MAX_PENDING_LOG_MESSAGES = 5000

    PAGE_COPY = {
        "parse": ("添加任务", "粘贴链接、选择分集和画质，然后加入下载队列"),
        "queue": ("下载队列", "查看进度、失败原因和已完成文件"),
        "settings": ("全局设置", "管理账号凭证、保存位置、并发和限速"),
        "logs": ("运行日志", "排查网络、解析和下载过程中的细节"),
    }

    def __init__(
        self,
        config_store: ConfigStore | None = None,
        manager: DownloadManager | None = None,
        api: BilibiliApi | None = None,
        ffmpeg: FFmpegService | None = None,
        initial_config: AppConfig | None = None,
        log_queue: queue.Queue[str] | None = None,
        emit_log=None,
        log_file_path: Path | None = None,
        log_dir_path: Path | None = None,
        auth_service: AuthService | None = None,
        icon_path: Path | None = None,
        started_at: float | None = None,
    ) -> None:
        initial_config = initial_config or AppConfig()
        self._started_at = started_at or time.perf_counter()
        self._apply_appearance(initial_config.appearance_mode)
        super().__init__(fg_color=PALETTE["app_bg"])

        self.config_store = config_store
        self.manager = manager
        self.api = api
        self.ffmpeg = ffmpeg
        self.current_config = initial_config
        self.log_queue = log_queue or queue.Queue()
        self.emit_log = emit_log or (lambda _message: None)
        self.log_file_path = log_file_path
        self.log_dir_path = log_dir_path
        self.auth_service = auth_service
        self.icon_path = icon_path

        self.current_page = "parse"
        self._active_nav_key: str | None = None
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.pages: dict[str, ctk.CTkFrame] = {}
        self._pending_log_messages: list[str] = []
        self._closing = False
        self._ready = False
        self._page_request_token = 0
        self._displayed_page = None
        self._displayed_key = None
        self._pending_queue_actions: set[str] = set()
        self._first_frame_at = None
        self.ui_tasks = UiTaskRunner(self)
        self.motion = motion_for(self)
        self.motion.set_enabled(initial_config.animations_enabled)

        self.title("BiliDownloader")
        scaling = self._get_window_scaling()
        width = min(1360, self.winfo_screenwidth() - 60)
        height = min(840, self.winfo_screenheight() - 90)
        self.minsize(1000, 640)
        left = max(0, round((self.winfo_screenwidth() - width) * scaling / 2))
        top = max(0, round((self.winfo_screenheight() - height) * scaling / 2) - 24)
        self.geometry(f"{width}x{height}+{left}+{top}")
        if self.icon_path is not None:
            try:
                self.iconbitmap(str(self.icon_path))
            except Exception:
                pass

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.grid_columnconfigure(0, minsize=238)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_sidebar()
        self._build_topbar()
        self._build_page_container()
        self._bind_shortcuts()
        self.toast = Toast(self)
        self.after_idle(self._record_first_frame)
        if manager is not None:
            self.after(20, self._finish_initialization)
        self.after(self.POLL_INTERVAL_MS, self._refresh_loop)

    def _record_first_frame(self) -> None:
        self._first_frame_at = time.perf_counter()

    def initialize_services(self, **services) -> None:
        if self._closing:
            return
        for name, value in services.items():
            setattr(self, name, value)
        self._apply_appearance(self.current_config.appearance_mode)
        self.motion.set_enabled(self.current_config.animations_enabled)
        self.loading_label.configure(text="正在准备链接解析界面…")
        self.after(20, self._finish_initialization)

    def _finish_initialization(self) -> None:
        if self._closing:
            return
        self._build_pages()
        self.add_task_page.update_config_preview(self.current_config)
        self.loading_progress.stop()
        self.loading_panel.place_forget()
        self._ready = True
        self.show_page(self.current_page)
        self.manager.set_auth_refresher(self._refresh_auth_session)
        first_frame = (self._first_frame_at or time.perf_counter()) - self._started_at
        self.emit_log(
            f"启动耗时：窗口首帧 {first_frame:.2f}s，下载页就绪 {time.perf_counter() - self._started_at:.2f}s"
        )
        self.after(800, self._start_silent_auth_check)

    def show_startup_error(self, error: Exception) -> None:
        if self._closing:
            return
        self.loading_progress.stop()
        self.loading_label.configure(text=f"启动失败：{error}\n请关闭后重试。", text_color=PALETTE["error"])

    def notify(self, message: str, kind: str = "success") -> None:
        if not self._closing:
            self.toast.show(message, kind)

    def _bind_shortcuts(self) -> None:
        for index, key in enumerate(self.PAGE_COPY, start=1):
            self.bind(f"<Control-Key-{index}>", lambda _event, key=key: self._page_shortcut(key))
        self.bind("<Control-l>", lambda _event: self._focus_page_field("parse", "url_input"))
        self.bind(
            "<Control-f>",
            lambda _event: self._focus_page_field(
                "logs" if self.current_page == "logs" else "queue",
                "filter_entry" if self.current_page == "logs" else "search_entry",
            ),
        )

    def _page_shortcut(self, key: str) -> str:
        self.show_page(key)
        return "break"

    def _focus_page_field(self, key: str, field: str) -> str:
        self.show_page(key)
        self._focus_when_ready(key, field)
        return "break"

    def _focus_when_ready(self, key: str, field: str, attempts: int = 100) -> None:
        if self._closing or self.current_page != key or attempts <= 0:
            return
        page = self.pages.get(key)
        if page is not None and page is self._displayed_page:
            getattr(page, field).focus_set()
        else:
            self.after(50, lambda: self._focus_when_ready(key, field, attempts - 1))

    def report_callback_exception(self, exception, value, trace) -> None:
        self.emit_log(f"界面操作异常：{exception.__name__}: {value}")
        if not self._ready:
            self.show_startup_error(value)
        elif hasattr(self, "toast"):
            self.notify("界面操作失败，请在运行日志中查看详细原因", "error")

    def _apply_appearance(self, mode: str) -> None:
        normalized = (mode or "system").strip().lower()
        mapping = {"system": "System", "light": "Light", "dark": "Dark"}
        ctk.set_appearance_mode(mapping.get(normalized, "System"))

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, fg_color=PALETTE["sidebar_bg"], corner_radius=0)
        sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(2, weight=1)

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=18, pady=(22, 18))
        TextLabel(
            brand,
            text="BiliDownloader",
            anchor="w",
            text_color=PALETTE["sidebar_heading"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=22, weight="bold"),
        ).pack(anchor="w")
        TextLabel(
            brand,
            text="B 站视频下载客户端",
            anchor="w",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).pack(anchor="w", pady=(4, 0))

        menu = ctk.CTkFrame(sidebar, fg_color="transparent")
        menu.grid(row=1, column=0, sticky="ew", padx=12)
        menu.grid_columnconfigure(0, weight=1)

        nav_items = (
            ("parse", "添加任务"),
            ("queue", "下载队列"),
            ("settings", "全局设置"),
            ("logs", "运行日志"),
        )
        for row, (page_key, title) in enumerate(nav_items):
            button = MotionButton(
                menu,
                text=title,
                anchor="w",
                height=42,
                corner_radius=CARD_RADIUS,
                fg_color="transparent",
                hover_color=PALETTE["sidebar_hover"],
                text_color=PALETTE["sidebar_text"],
                font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
                command=lambda key=page_key: self.show_page(key),
            )
            button.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            self.nav_buttons[page_key] = button

        self.nav_indicator = ctk.CTkFrame(menu, width=3, height=22, corner_radius=2, fg_color=PALETTE["primary"])
        self._nav_indicator_y = 10.0

        footer = ctk.CTkFrame(sidebar, fg_color=PALETTE["sidebar_alt"], corner_radius=PANEL_RADIUS)
        footer.grid(row=3, column=0, sticky="ew", padx=12, pady=(12, 18))
        footer.grid_columnconfigure(0, weight=1)
        TextLabel(
            footer,
            text="Ctrl+L 输入链接",
            anchor="w",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 2))
        TextLabel(
            footer,
            text="Ctrl+F 搜索 · Ctrl+1–4 切页",
            anchor="w",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))

    def _build_topbar(self) -> None:
        topbar = ctk.CTkFrame(self, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        topbar.grid(row=0, column=1, sticky="ew", padx=18, pady=(18, 12))
        topbar.grid_columnconfigure(0, weight=1)

        copy = ctk.CTkFrame(topbar, fg_color="transparent")
        copy.grid(row=0, column=0, sticky="ew", padx=18, pady=14)
        copy.grid_columnconfigure(0, weight=1)

        self.page_title_var = ctk.StringVar(value=self.PAGE_COPY["parse"][0])
        self.page_subtitle_var = ctk.StringVar(value=self.PAGE_COPY["parse"][1])
        TextLabel(
            copy,
            textvariable=self.page_title_var,
            anchor="w",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=24, weight="bold"),
        ).grid(row=0, column=0, sticky="ew")
        TextLabel(
            copy,
            textvariable=self.page_subtitle_var,
            anchor="w",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=1, column=0, sticky="ew", pady=(3, 0))

        stats_frame = ctk.CTkFrame(topbar, fg_color="transparent")
        stats_frame.grid(row=0, column=1, sticky="e", padx=12, pady=12)

        self.total_speed_var = ctk.StringVar(value="0 B/s")
        self.active_var = ctk.StringVar(value="0")
        self.queue_var = ctk.StringVar(value="0")
        self._dashboard_labels = {}

        stats = (
            ("总速度", self.total_speed_var),
            ("进行中", self.active_var),
            ("排队", self.queue_var),
        )
        for column, (label, variable) in enumerate(stats):
            self._build_stat_tile(stats_frame, column, label, variable)

    def _build_stat_tile(self, master, column: int, label: str, variable: ctk.StringVar) -> None:
        value_width = 116 if column == 0 else 34
        card = ctk.CTkFrame(master, fg_color=PALETTE["surface"], corner_radius=CARD_RADIUS)
        card.grid(row=0, column=column, padx=4)
        TextLabel(
            card, text=label, width=value_width, anchor="w", text_color=PALETTE["muted"], font=ctk.CTkFont(size=11)
        ).pack(anchor="w", padx=12, pady=(8, 1))
        value_label = TextLabel(
            card,
            text=variable.get(),
            width=value_width,
            anchor="w",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
        )
        value_label.pack(anchor="w", padx=12, pady=(0, 8))
        self._dashboard_labels[str(variable)] = value_label

    def _build_page_container(self) -> None:
        self.page_container = ctk.CTkFrame(self, fg_color=PALETTE["app_bg"])
        self.page_container.grid(row=1, column=1, sticky="nsew", padx=18, pady=(0, 18))
        self.page_container.grid_rowconfigure(0, weight=1)
        self.page_container.grid_columnconfigure(0, weight=1)

        self.loading_panel = ctk.CTkFrame(self.page_container, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        self.loading_panel.place(relwidth=1, relheight=1)
        self.loading_label = TextLabel(
            self.loading_panel,
            text="正在准备下载服务…",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=15),
            wraplength=600,
        )
        self.loading_label.place(relx=0.5, rely=0.45, anchor="center")
        self.loading_progress = ctk.CTkProgressBar(
            self.loading_panel, width=180, height=4, mode="indeterminate", progress_color=PALETTE["primary"]
        )
        self.loading_progress.place(relx=0.5, rely=0.45, y=36, anchor="center")
        self.loading_progress.start()

    def _build_pages(self) -> None:
        from bili_client.ui.add_task_page import AddTaskPage

        self.add_task_page = AddTaskPage(
            self.page_container,
            api=self.api,
            manager=self.manager,
            get_config=self._get_current_config,
            emit_log=self.emit_log,
            show_queue_page=lambda: self.show_page("queue"),
            open_settings_page=lambda: self.show_page("settings"),
            refresh_auth_session=self._refresh_auth_session,
        )
        self.pages = {"parse": self.add_task_page}

    def _ensure_page(self, page_key: str) -> ctk.CTkFrame:
        existing = self.pages.get(page_key)
        if existing is not None:
            return existing

        if page_key == "queue":
            from bili_client.ui.queue_page import QueuePage

            page = QueuePage(
                self.page_container,
                on_cancel_task=self._cancel_task,
                on_retry_task=self._retry_task,
                on_remove_task=self._remove_task,
                on_retry_failed=self._retry_failed_tasks,
                on_clear_finished=self._clear_finished_tasks,
                on_open_dir=self._open_dir,
            )
        elif page_key == "settings":
            from bili_client.ui.settings_page import SettingsPage

            page = SettingsPage(
                self.page_container,
                on_save_config=self._save_config,
                auth_service=self.auth_service,
            )
            page.load_config(self.current_config)
        elif page_key == "logs":
            from bili_client.ui.log_page import LogPage

            page = LogPage(
                self.page_container,
                log_file_path=self.log_file_path,
                log_dir_path=self.log_dir_path,
                on_show_failed_tasks=self._show_failed_tasks,
            )
            if self._pending_log_messages:
                page.append_messages(self._pending_log_messages)
                self._pending_log_messages.clear()
        else:
            return self.add_task_page

        self.pages[page_key] = page
        return page

    def _get_current_config(self) -> AppConfig:
        return self.current_config

    def show_page(self, page_key: str) -> None:
        target = page_key if page_key in self.PAGE_COPY else "parse"
        page_changed = target != self.current_page or self._active_nav_key != target
        self.current_page = target
        if not self._ready:
            return
        if not page_changed and self._displayed_page is self.pages.get(target):
            if target == "queue":
                snapshot = self.manager.snapshot()
                self.pages[target].update_snapshot(snapshot["metrics"], snapshot["tasks"])
            return
        self._page_request_token += 1
        token = self._page_request_token
        title, subtitle = self.PAGE_COPY[target]
        if self.page_title_var.get() != title:
            self.page_title_var.set(title)
        if self.page_subtitle_var.get() != subtitle:
            self.page_subtitle_var.set(subtitle)

        self._sync_nav_buttons()
        if target not in self.pages:
            self.loading_label.configure(text="正在准备页面…", text_color=PALETTE["muted"])
            self.loading_panel.place(relwidth=1, relheight=1)
            self.loading_panel.lift()
            self.loading_progress.start()
            self.after(20, lambda: self._present_page(target, token))
        else:
            self._present_page(target, token)

    def _present_page(self, target: str, token: int) -> None:
        if self._closing or token != self._page_request_token:
            return
        page = self._ensure_page(target)
        self.motion.cancel("page")
        previous = self._displayed_page
        previous_key = self._displayed_key
        for existing in self.pages.values():
            if existing is not page and existing is not previous:
                existing.place_forget()
            if hasattr(existing, "set_active"):
                existing.set_active(existing is page)
        if target == "queue":
            snapshot = self.manager.snapshot()
            page.update_snapshot(snapshot["metrics"], snapshot["tasks"])
        self.loading_progress.stop()
        self.loading_panel.place_forget()
        self._displayed_page = page
        self._displayed_key = target
        page.place(x=0, y=0, relwidth=1, relheight=1)
        page.lift()
        self.toast.lift()

        def finish() -> None:
            if previous is not None and previous is not page:
                previous.place_forget()

        # Move a fully sized frame; never resize/rebuild its children per frame.
        direction = (
            1
            if previous_key is None or tuple(self.PAGE_COPY).index(target) >= tuple(self.PAGE_COPY).index(previous_key)
            else -1
        )
        self.motion.tween(
            "page",
            18 * direction if previous is not None else 0,
            0,
            lambda offset: page.place(x=round(offset), y=0, relwidth=1, relheight=1),
            180,
            finish,
        )

    def _sync_nav_buttons(self) -> None:
        if self._active_nav_key == self.current_page:
            return

        previous = self.nav_buttons.get(self._active_nav_key or "")
        if previous is not None:
            previous.configure(
                fg_color="transparent",
                hover_color=PALETTE["sidebar_hover"],
                text_color=PALETTE["sidebar_text"],
            )

        current = self.nav_buttons.get(self.current_page)
        if current is not None:
            current.configure(
                fg_color=PALETTE["primary_soft"],
                hover_color=PALETTE["primary_soft"],
                text_color=PALETTE["primary_hover"],
            )
        index = tuple(self.PAGE_COPY).index(self.current_page)
        self.nav_indicator.place(x=0, y=round(self._nav_indicator_y))
        self.nav_indicator.lift()
        self.motion.tween("navigation", self._nav_indicator_y, index * 48 + 10, self._move_nav_indicator, 200)
        self._active_nav_key = self.current_page

    def _move_nav_indicator(self, value: float) -> None:
        self._nav_indicator_y = value
        self.nav_indicator.place(x=0, y=round(value))

    def _save_config(self, config: AppConfig) -> None:
        if self.auth_service is not None and config.sessdata != self.current_config.sessdata:
            self.auth_service.set_sessdata(config.sessdata)
        self.current_config = config
        self.config_store.save(config)
        self.manager.update_config(config)
        self.api.http_client.set_steady_mode(config.steady_mode)
        self.ffmpeg.set_command(config.ffmpeg_path)
        self._apply_appearance(config.appearance_mode)
        self.motion.set_enabled(config.animations_enabled)
        self._refresh_theme()
        self.add_task_page.update_config_preview(config)
        self.emit_log("全局设置已保存")

    def _start_silent_auth_check(self) -> None:
        if self.auth_service is None:
            return
        self.ui_tasks.run("auth-startup-check", lambda: self._refresh_auth_session(force=True))

    def _refresh_auth_session(self, force: bool = False) -> str:
        from bili_client.services.auth_service import AuthError

        if self.auth_service is None:
            return self.current_config.sessdata

        try:
            session = self.auth_service.ensure_latest_session(self.current_config.sessdata, force=force)
        except AuthError as exc:
            self.emit_log(f"登录态刷新失败：{exc}")
            return self.current_config.sessdata
        except Exception as exc:
            self.emit_log(f"登录态检查失败：{exc}")
            return self.current_config.sessdata

        if session is None or not session.sessdata:
            return self.current_config.sessdata

        if session.sessdata != self.current_config.sessdata:
            self.ui_tasks.post(lambda sessdata=session.sessdata: self._accept_refreshed_session(sessdata))

        return session.sessdata

    def _accept_refreshed_session(self, sessdata: str) -> None:
        if self._closing or sessdata == self.current_config.sessdata:
            return
        if self.auth_service is not None:
            session = self.auth_service.load_session()
            if session is None or session.sessdata != sessdata:
                return
        updated = replace(self.current_config, sessdata=sessdata)
        self.current_config = updated
        self.config_store.save(updated)
        self.manager.update_config(updated)
        self.emit_log("已同步最新 SESSDATA")
        self._apply_refreshed_config_to_ui(updated)

    def _apply_refreshed_config_to_ui(self, config: AppConfig) -> None:
        self.add_task_page.update_config_preview(config)
        settings_page = self.pages.get("settings")
        if settings_page is not None and not settings_page.has_unsaved_changes():
            settings_page.load_config(config)

    def _refresh_theme(self) -> None:
        self.configure(fg_color=PALETTE["app_bg"])
        self._active_nav_key = None
        self._sync_nav_buttons()

    def _cancel_task(self, task_id: str) -> None:
        self._queue_action(f"cancel-{task_id}", lambda: self.manager.cancel_task(task_id), "已请求取消任务")

    def _retry_task(self, task_id: str) -> None:
        self._queue_action(f"retry-{task_id}", lambda: self.manager.retry_task(task_id), "任务已重新加入队列")

    def _remove_task(self, task_id: str) -> None:
        self._queue_action(
            f"remove-{task_id}", lambda: self.manager.remove_task(task_id), "任务记录已移除，下载文件保留"
        )

    def _retry_failed_tasks(self) -> None:
        self._queue_action("retry-all", self.manager.retry_terminal_tasks, "可重试任务已加入队列")

    def _clear_finished_tasks(self) -> None:
        self._queue_action("clear-finished", self.manager.clear_finished, "已清理结束记录，下载文件保留")

    def _queue_action(self, key: str, work, success_message: str) -> None:
        if key in self._pending_queue_actions:
            return
        self._pending_queue_actions.add(key)

        def finished(result) -> None:
            if self.current_page == "queue":
                self.show_page("queue")
            self.notify(
                success_message if result else "当前没有可执行的任务，状态可能已更新",
                "success" if result else "warning",
            )

        self.ui_tasks.run(
            key,
            work,
            on_success=finished,
            on_error=lambda exc: self.notify(f"操作失败：{exc}", "error"),
            on_finally=lambda: self._pending_queue_actions.discard(key),
        )

    def _show_failed_tasks(self) -> None:
        self.show_page("queue")
        self._filter_failed_when_ready()

    def _filter_failed_when_ready(self) -> None:
        if self._closing or self.current_page != "queue":
            return
        page = self.pages.get("queue")
        if page is not None and page is self._displayed_page:
            page.show_failed_tasks()
        else:
            self.after(50, self._filter_failed_when_ready)

    def _open_dir(self, path: str) -> None:
        if not path:
            messagebox.showerror("打开失败", "当前任务没有可用目录。")
            return
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
            os.startfile(path)
        except Exception as exc:
            messagebox.showerror("打开失败", f"无法打开目录：{exc}")

    def _refresh_loop(self) -> None:
        if self._closing:
            return
        self._drain_logs()
        if not self._ready:
            self.after(self.POLL_INTERVAL_MS, self._refresh_loop)
            return
        queue_page = self.pages.get("queue")
        if self.current_page == "queue" and queue_page is not None:
            snapshot = self.manager.snapshot()
            metrics = snapshot["metrics"]
            queue_page.update_snapshot(snapshot["metrics"], snapshot["tasks"])
        else:
            metrics = self.manager.metrics()
        self._update_dashboard(metrics)
        interval = self.POLL_INTERVAL_MS if metrics["active_count"] or metrics["queued_count"] else 1000
        if self.state() == "iconic":
            interval = 1500
        self.after(interval, self._refresh_loop)

    def _drain_logs(self) -> None:
        messages: list[str] = []
        for _ in range(200):
            try:
                messages.append(self.log_queue.get_nowait())
            except queue.Empty:
                break

        log_page = self.pages.get("logs")
        if log_page is not None:
            log_page.append_messages(messages)
        elif messages:
            self._pending_log_messages.extend(messages)
            overflow = len(self._pending_log_messages) - self.MAX_PENDING_LOG_MESSAGES
            if overflow > 0:
                del self._pending_log_messages[:overflow]

    def _update_dashboard(self, metrics: dict) -> None:
        for variable, value in (
            (self.total_speed_var, format_speed(metrics["total_speed_bps"])),
            (self.active_var, str(metrics["active_count"])),
            (self.queue_var, str(metrics["queued_count"])),
        ):
            if variable.get() != value:
                variable.set(value)
                self._dashboard_labels[str(variable)].configure(text=value)

    def bring_to_front(self) -> None:
        if self._closing:
            return
        self.deiconify()
        try:
            self.state("normal")
        except Exception:
            pass
        self.lift()
        self.focus_force()
        try:
            self.attributes("-topmost", True)
            self.after(180, lambda: self.attributes("-topmost", False))
        except Exception:
            pass

    def _on_close(self) -> None:
        if self._closing:
            return
        if self.manager is not None and self.manager.has_unfinished_tasks():
            choice = messagebox.askyesnocancel(
                "仍有下载任务",
                "还有下载任务没有结束。\n\n选择“是”：停止任务并退出。\n选择“否”：最小化窗口，任务继续运行。\n选择“取消”：留在当前界面。",
            )
            if choice is None:
                return
            if choice is False:
                self.iconify()
                return

        self._closing = True
        self.motion.close()
        self.loading_progress.stop()
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.withdraw()
        if self.manager is None:
            self.destroy()
        else:
            self.ui_tasks.run("app-shutdown", self.manager.shutdown, on_success=lambda _result: self.destroy())

    def destroy(self) -> None:
        self._closing = True
        self.motion.close()
        self.ui_tasks._dispatcher.close()
        # Tcl timers outlive their Python widget commands unless cancelled.
        # This also prevents stray callbacks when GUI tests create another root.
        for identifier in self.tk.call("after", "info"):
            self.tk.call("after", "cancel", identifier)
        super().destroy()
