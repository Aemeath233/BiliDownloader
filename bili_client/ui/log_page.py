from __future__ import annotations

import os
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from bili_client.ui.styles import BUTTON_HEIGHT, CARD_RADIUS, FONT_FAMILY, PALETTE, PANEL_RADIUS, button_style


LOG_FILTERS = {
    "全部": "all",
    "错误": "error",
    "下载": "download",
    "登录": "auth",
}


class LogPage(ctk.CTkFrame):
    def __init__(self, master, log_file_path: Path, log_dir_path: Path, on_show_failed_tasks=None) -> None:
        super().__init__(master, fg_color="transparent")
        self.auto_scroll_var = ctk.BooleanVar(value=True)
        self.filter_var = ctk.StringVar(value="")
        self.log_file_path = log_file_path
        self.log_dir_path = log_dir_path
        self.on_show_failed_tasks = on_show_failed_tasks
        self.all_messages: list[str] = []
        self._has_recent_error = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        panel = ctk.CTkFrame(self, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 10))
        header.grid_columnconfigure(0, weight=1)

        copy = ctk.CTkFrame(header, fg_color="transparent")
        copy.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            copy,
            text="日志输出",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            copy,
            text=f"文件：{self.log_file_path}",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
        ).pack(anchor="w", pady=(3, 0))

        action_row = ctk.CTkFrame(header, fg_color="transparent")
        action_row.grid(row=0, column=1, sticky="e")

        ctk.CTkCheckBox(
            action_row,
            text="自动滚动",
            variable=self.auto_scroll_var,
            onvalue=True,
            offvalue=False,
            fg_color=PALETTE["primary"],
            hover_color=PALETTE["primary_hover"],
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=0, column=0, padx=(0, 10))
        ctk.CTkButton(
            action_row,
            text="复制日志",
            command=self.copy_visible_logs,
            width=88,
            height=BUTTON_HEIGHT,
            corner_radius=CARD_RADIUS,
            **button_style("secondary"),
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            action_row,
            text="打开文件",
            command=self.open_log_file,
            width=88,
            height=BUTTON_HEIGHT,
            corner_radius=CARD_RADIUS,
            **button_style("secondary"),
        ).grid(row=0, column=2, padx=(0, 8))
        ctk.CTkButton(
            action_row,
            text="打开目录",
            command=self.open_log_dir,
            width=88,
            height=BUTTON_HEIGHT,
            corner_radius=CARD_RADIUS,
            **button_style("secondary"),
        ).grid(row=0, column=3, padx=(0, 8))
        ctk.CTkButton(
            action_row,
            text="清空界面",
            command=self.clear_logs,
            width=88,
            height=BUTTON_HEIGHT,
            corner_radius=CARD_RADIUS,
            **button_style("secondary"),
        ).grid(row=0, column=4)

        filter_row = ctk.CTkFrame(panel, fg_color="transparent")
        filter_row.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 10))
        filter_row.grid_columnconfigure(1, weight=1)

        self.filter_segment = ctk.CTkSegmentedButton(
            filter_row,
            values=list(LOG_FILTERS.keys()),
            command=lambda _value: self._render_visible_logs(),
            height=34,
            corner_radius=CARD_RADIUS,
            fg_color=PALETTE["surface"],
            selected_color=PALETTE["primary"],
            selected_hover_color=PALETTE["primary_hover"],
            unselected_color=PALETTE["surface"],
            unselected_hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
        )
        self.filter_segment.grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.filter_segment.set("全部")

        self.filter_entry = ctk.CTkEntry(
            filter_row,
            textvariable=self.filter_var,
            placeholder_text="筛选任务标题、错误关键词或 URL",
            height=34,
            fg_color=PALETTE["input_bg"],
            border_color=PALETTE["border"],
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.filter_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.filter_entry.bind("<KeyRelease>", lambda _event: self._render_visible_logs())
        self.filter_entry.bind("<<Paste>>", lambda _event: self.after(50, self._render_visible_logs))

        self.failed_tasks_button = ctk.CTkButton(
            filter_row,
            text="查看失败任务",
            command=self._show_failed_tasks,
            width=108,
            height=BUTTON_HEIGHT,
            corner_radius=CARD_RADIUS,
            **button_style("secondary"),
            state="disabled",
        )
        self.failed_tasks_button.grid(row=0, column=2, sticky="e")

        self.log_text = ctk.CTkTextbox(
            panel,
            fg_color=PALETTE["surface"],
            corner_radius=CARD_RADIUS,
            border_width=1,
            border_color=PALETTE["border"],
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.log_text.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.log_text.configure(state="disabled")

    def append_messages(self, messages: list[str]) -> None:
        if not messages:
            return

        self.all_messages.extend(messages)
        if any(self._is_error_message(message) for message in messages):
            self._has_recent_error = True
            self.failed_tasks_button.configure(state="normal")
        self._render_visible_logs(keep_position=True)

    def _render_visible_logs(self, keep_position: bool = False) -> None:
        visible_messages = [message for message in self.all_messages if self._passes_filter(message)]
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        for message in visible_messages:
            self.log_text.insert("end", message + "\n")
        if self.auto_scroll_var.get():
            self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _passes_filter(self, message: str) -> bool:
        filter_key = LOG_FILTERS.get(self.filter_segment.get(), "all")
        keyword = self.filter_var.get().strip().lower()
        lowered = message.lower()
        if keyword and keyword not in lowered:
            return False
        if filter_key == "error":
            return self._is_error_message(message)
        if filter_key == "download":
            return any(token in message for token in ("任务", "下载", "FFmpeg", "流", "队列"))
        if filter_key == "auth":
            return any(token in message for token in ("登录", "SESSDATA", "Cookie", "扫码"))
        return True

    def _is_error_message(self, message: str) -> bool:
        lowered = message.lower()
        return any(token in lowered for token in ("error", "exception", "failed")) or any(
            token in message for token in ("失败", "错误", "失效", "不可用")
        )

    def copy_visible_logs(self) -> None:
        content = "\n".join(message for message in self.all_messages if self._passes_filter(message))
        self.clipboard_clear()
        self.clipboard_append(content)

    def clear_logs(self) -> None:
        self.all_messages.clear()
        self._has_recent_error = False
        self.failed_tasks_button.configure(state="disabled")
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def open_log_file(self) -> None:
        try:
            self.log_file_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.log_file_path.exists():
                self.log_file_path.touch()
            os.startfile(self.log_file_path)
        except Exception as exc:
            messagebox.showerror("打开失败", f"无法打开日志文件：{exc}")

    def open_log_dir(self) -> None:
        try:
            self.log_dir_path.mkdir(parents=True, exist_ok=True)
            os.startfile(self.log_dir_path)
        except Exception as exc:
            messagebox.showerror("打开失败", f"无法打开日志目录：{exc}")

    def _show_failed_tasks(self) -> None:
        if self.on_show_failed_tasks is not None:
            self.on_show_failed_tasks()
