from __future__ import annotations

import os
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from bili_client.ui.styles import CARD_RADIUS, FONT_FAMILY, PALETTE, PANEL_RADIUS


class LogPage(ctk.CTkFrame):
    def __init__(self, master, log_file_path: Path, log_dir_path: Path) -> None:
        super().__init__(master, fg_color="transparent")
        self.auto_scroll_var = ctk.BooleanVar(value=True)
        self.log_file_path = log_file_path
        self.log_dir_path = log_dir_path

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        panel = ctk.CTkFrame(self, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)

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
            text="打开目录",
            command=self.open_log_dir,
            width=88,
            height=34,
            corner_radius=CARD_RADIUS,
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            action_row,
            text="清空界面",
            command=self.clear_logs,
            width=88,
            height=34,
            corner_radius=CARD_RADIUS,
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
        ).grid(row=0, column=2)

        self.log_text = ctk.CTkTextbox(
            panel,
            fg_color=PALETTE["surface"],
            corner_radius=CARD_RADIUS,
            border_width=1,
            border_color=PALETTE["border"],
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.log_text.configure(state="disabled")

    def append_messages(self, messages: list[str]) -> None:
        if not messages:
            return

        self.log_text.configure(state="normal")
        for message in messages:
            self.log_text.insert("end", message + "\n")
        if self.auto_scroll_var.get():
            self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_logs(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def open_log_dir(self) -> None:
        try:
            self.log_dir_path.mkdir(parents=True, exist_ok=True)
            os.startfile(self.log_dir_path)
        except Exception as exc:
            messagebox.showerror("打开失败", f"无法打开日志目录：{exc}")
