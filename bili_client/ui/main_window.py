from __future__ import annotations

import os
import queue
import threading
from dataclasses import replace
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from bili_client.core.formatters import format_speed
from bili_client.core.models import AppConfig
from bili_client.services.bili_api import BilibiliApi
from bili_client.services.auth_service import AuthError, AuthService
from bili_client.services.config_store import ConfigStore
from bili_client.services.download_manager import DownloadManager
from bili_client.services.ffmpeg import FFmpegService
from bili_client.ui.add_task_page import AddTaskPage
from bili_client.ui.log_page import LogPage
from bili_client.ui.queue_page import QueuePage
from bili_client.ui.settings_page import SettingsPage
from bili_client.ui.styles import CARD_RADIUS, FONT_FAMILY, PALETTE, PANEL_RADIUS


class MainWindow(ctk.CTk):
    POLL_INTERVAL_MS = 400

    PAGE_COPY = {
        "parse": ("添加任务", "粘贴链接、选择分集和画质，然后加入下载队列"),
        "queue": ("下载队列", "查看进度、失败原因和已完成文件"),
        "settings": ("全局设置", "管理账号凭证、保存位置、并发和限速"),
        "logs": ("运行日志", "排查网络、解析和下载过程中的细节"),
    }

    def __init__(
        self,
        config_store: ConfigStore,
        manager: DownloadManager,
        api: BilibiliApi,
        ffmpeg: FFmpegService,
        initial_config: AppConfig,
        log_queue: "queue.Queue[str]",
        emit_log,
        log_file_path: Path,
        log_dir_path: Path,
        auth_service: AuthService | None = None,
        icon_path: Path | None = None,
    ) -> None:
        self._apply_appearance(initial_config.appearance_mode)
        super().__init__(fg_color=PALETTE["app_bg"])

        self.config_store = config_store
        self.manager = manager
        self.api = api
        self.ffmpeg = ffmpeg
        self.current_config = initial_config
        self.log_queue = log_queue
        self.emit_log = emit_log
        self.log_file_path = log_file_path
        self.log_dir_path = log_dir_path
        self.auth_service = auth_service
        self.icon_path = icon_path

        self.current_page = "parse"
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.pages: dict[str, ctk.CTkFrame] = {}
        self._closing = False

        self.title("BiliDownloader")
        self.geometry("1480x900")
        self.minsize(1160, 760)
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
        self._build_pages()

        self.settings_page.load_config(initial_config)
        self.add_task_page.update_config_preview(initial_config)
        self.show_page("parse")
        self.manager.set_auth_refresher(self._refresh_auth_session)

        self.after(800, self._start_silent_auth_check)
        self.after(self.POLL_INTERVAL_MS, self._refresh_loop)

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
        ctk.CTkLabel(
            brand,
            text="BiliDownloader",
            anchor="w",
            text_color=PALETTE["sidebar_heading"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=22, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
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
            button = ctk.CTkButton(
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

        footer = ctk.CTkFrame(sidebar, fg_color=PALETTE["sidebar_alt"], corner_radius=PANEL_RADIUS)
        footer.grid(row=3, column=0, sticky="ew", padx=12, pady=(12, 18))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            footer,
            text="下载状态会自动刷新",
            anchor="w",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 2))
        ctk.CTkLabel(
            footer,
            text="有任务时关闭前会确认",
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

        self.page_title_var = ctk.StringVar(value="")
        self.page_subtitle_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            copy,
            textvariable=self.page_title_var,
            anchor="w",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=24, weight="bold"),
        ).grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
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

        stats = (
            ("总速度", self.total_speed_var),
            ("进行中", self.active_var),
            ("排队", self.queue_var),
        )
        for column, (label, variable) in enumerate(stats):
            self._build_stat_tile(stats_frame, column, label, variable)

    def _build_stat_tile(self, master, column: int, label: str, variable: ctk.StringVar) -> None:
        card = ctk.CTkFrame(master, fg_color=PALETTE["surface"], corner_radius=CARD_RADIUS)
        card.grid(row=0, column=column, padx=4)
        ctk.CTkLabel(card, text=label, text_color=PALETTE["muted"], font=ctk.CTkFont(size=11)).pack(
            anchor="w", padx=12, pady=(8, 1)
        )
        ctk.CTkLabel(
            card,
            textvariable=variable,
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
        ).pack(anchor="w", padx=12, pady=(0, 8))

    def _build_pages(self) -> None:
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.grid(row=1, column=1, sticky="nsew", padx=18, pady=(0, 18))
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.add_task_page = AddTaskPage(
            container,
            api=self.api,
            manager=self.manager,
            get_config=self._get_current_config,
            emit_log=self.emit_log,
            show_queue_page=lambda: self.show_page("queue"),
            open_settings_page=lambda: self.show_page("settings"),
            refresh_auth_session=self._refresh_auth_session,
        )
        self.queue_page = QueuePage(
            container,
            on_cancel_task=self._cancel_task,
            on_retry_task=self._retry_task,
            on_remove_task=self._remove_task,
            on_retry_failed=self._retry_failed_tasks,
            on_clear_finished=self._clear_finished_tasks,
            on_open_dir=self._open_dir,
        )
        self.settings_page = SettingsPage(container, on_save_config=self._save_config, auth_service=self.auth_service)
        self.log_page = LogPage(
            container,
            log_file_path=self.log_file_path,
            log_dir_path=self.log_dir_path,
            on_show_failed_tasks=self._show_failed_tasks,
        )

        self.pages = {
            "parse": self.add_task_page,
            "queue": self.queue_page,
            "settings": self.settings_page,
            "logs": self.log_page,
        }
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")

    def _get_current_config(self) -> AppConfig:
        return self.current_config

    def show_page(self, page_key: str) -> None:
        target = page_key if page_key in self.pages else "parse"
        self.current_page = target
        self.pages[target].tkraise()

        if target == "settings" and not self.settings_page.has_unsaved_changes():
            self.settings_page.load_config(self.current_config)

        title, subtitle = self.PAGE_COPY[target]
        self.page_title_var.set(title)
        self.page_subtitle_var.set(subtitle)
        self._sync_nav_buttons()

    def _sync_nav_buttons(self) -> None:
        for key, button in self.nav_buttons.items():
            if key == self.current_page:
                button.configure(
                    fg_color=PALETTE["primary"],
                    hover_color=PALETTE["primary_hover"],
                    text_color=PALETTE["text_inverse"],
                )
            else:
                button.configure(
                    fg_color="transparent",
                    hover_color=PALETTE["sidebar_hover"],
                    text_color=PALETTE["sidebar_text"],
                )

    def _save_config(self, config: AppConfig) -> None:
        self.current_config = config
        self.config_store.save(config)
        self.manager.update_config(config)
        self.ffmpeg.set_command(config.ffmpeg_path)
        self._apply_appearance(config.appearance_mode)
        self._refresh_theme()
        self.add_task_page.update_config_preview(config)
        self.emit_log("全局设置已保存")

    def _start_silent_auth_check(self) -> None:
        if self.auth_service is None:
            return
        threading.Thread(
            target=lambda: self._refresh_auth_session(force=True),
            daemon=True,
            name="auth-startup-check",
        ).start()

    def _refresh_auth_session(self, force: bool = False) -> str:
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
            updated = replace(self.current_config, sessdata=session.sessdata)
            self.current_config = updated
            self.config_store.save(updated)
            self.manager.update_config(updated)
            self.emit_log("已同步最新 SESSDATA")
            self.after(0, lambda config=updated: self._apply_refreshed_config_to_ui(config))

        return session.sessdata

    def _apply_refreshed_config_to_ui(self, config: AppConfig) -> None:
        self.add_task_page.update_config_preview(config)
        if not self.settings_page.has_unsaved_changes():
            self.settings_page.load_config(config)

    def _refresh_theme(self) -> None:
        self.configure(fg_color=PALETTE["app_bg"])
        self._sync_nav_buttons()

    def _cancel_task(self, task_id: str) -> None:
        self.manager.cancel_task(task_id)
        self.show_page("queue")

    def _retry_task(self, task_id: str) -> None:
        new_task_id = self.manager.retry_task(task_id)
        if new_task_id:
            self.show_page("queue")
        else:
            messagebox.showinfo("不能重试", "这个任务当前不能重试。")

    def _remove_task(self, task_id: str) -> None:
        removed = self.manager.remove_task(task_id)
        if removed:
            self.show_page("queue")
        else:
            messagebox.showinfo("不能移除", "进行中的任务需要先取消，结束后再移除。")

    def _retry_failed_tasks(self) -> None:
        count = self.manager.retry_terminal_tasks()
        if count:
            self.show_page("queue")
        else:
            messagebox.showinfo("没有可重试任务", "当前没有失败或已取消的任务。")

    def _clear_finished_tasks(self) -> None:
        count = self.manager.clear_finished()
        if count:
            self.show_page("queue")
        else:
            messagebox.showinfo("没有可清理任务", "当前没有已结束的任务。")

    def _show_failed_tasks(self) -> None:
        self.show_page("queue")
        self.queue_page.show_failed_tasks()

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
        snapshot = self.manager.snapshot()
        self._update_dashboard(snapshot["metrics"])
        self.queue_page.update_snapshot(snapshot["metrics"], snapshot["tasks"])
        self.after(self.POLL_INTERVAL_MS, self._refresh_loop)

    def _drain_logs(self) -> None:
        messages: list[str] = []
        while True:
            try:
                messages.append(self.log_queue.get_nowait())
            except queue.Empty:
                break

        self.log_page.append_messages(messages)

    def _update_dashboard(self, metrics: dict) -> None:
        self.total_speed_var.set(format_speed(metrics["total_speed_bps"]))
        self.active_var.set(str(metrics["active_count"]))
        self.queue_var.set(str(metrics["queued_count"]))

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
        if self.manager.has_unfinished_tasks():
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
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.withdraw()
        threading.Thread(target=self._shutdown_then_destroy, daemon=True, name="app-shutdown").start()

    def _shutdown_then_destroy(self) -> None:
        self.manager.shutdown()
        try:
            self.after(0, self.destroy)
        except RuntimeError:
            pass
