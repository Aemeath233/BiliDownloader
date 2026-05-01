from __future__ import annotations

import shutil
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from bili_client.core.models import AppConfig
from bili_client.services.auth_service import AuthService, AuthSession
from bili_client.services.ffmpeg import FFmpegService
from bili_client.ui.auth_dialog import QrLoginDialog
from bili_client.ui.styles import CARD_RADIUS, FONT_FAMILY, PALETTE, PANEL_RADIUS


APPEARANCE_LABELS = {
    "system": "跟随系统",
    "light": "浅色",
    "dark": "深色",
}
APPEARANCE_VALUES = {label: value for value, label in APPEARANCE_LABELS.items()}


class SettingsPage(ctk.CTkFrame):
    def __init__(self, master, on_save_config, auth_service: AuthService | None = None) -> None:
        super().__init__(master, fg_color="transparent")
        self.on_save_config = on_save_config
        self.auth_service = auth_service
        self._loaded_form_values: dict[str, str] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.scroll_frame = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.scroll_frame.grid(row=0, column=0, sticky="nsew")
        self.scroll_frame.grid_columnconfigure(0, weight=1)
        self.scroll_frame.grid_columnconfigure(1, weight=1)

        self._build_account_panel()
        self._build_runtime_panel()
        self._build_status_panel()
        self._build_action_bar()
        self._bind_form_events()

    def _build_account_panel(self) -> None:
        panel = ctk.CTkFrame(self.scroll_frame, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 12))
        panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            panel,
            text="账号与保存",
            anchor="w",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 6))

        form = ctk.CTkFrame(panel, fg_color=PALETTE["surface"], corner_radius=CARD_RADIUS)
        form.grid(row=1, column=0, sticky="ew", padx=18, pady=(8, 18))
        form.grid_columnconfigure(0, weight=1)

        self.sessdata_entry = self._entry_block(form, 0, "SESSDATA", "可选，高画质或会员内容可能需要", show="*")
        self.save_path_entry = self._entry_block(form, 1, "保存目录", "默认保存到 video")

        ctk.CTkButton(
            form,
            text="选择保存目录",
            command=self._pick_save_dir,
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
            height=36,
            corner_radius=CARD_RADIUS,
        ).grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 14))

        auth_row = ctk.CTkFrame(form, fg_color=PALETTE["surface_alt"], corner_radius=CARD_RADIUS)
        auth_row.grid(row=5, column=0, sticky="ew", padx=14, pady=(0, 14))
        auth_row.grid_columnconfigure(0, weight=1)

        self.auth_status_label = ctk.CTkLabel(
            auth_row,
            text="可用扫码登录自动保存并刷新 SESSDATA",
            anchor="w",
            justify="left",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.auth_status_label.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))

        self.qr_login_button = ctk.CTkButton(
            auth_row,
            text="扫码登录 / 更新 SESSDATA",
            command=self._open_qr_login,
            fg_color=PALETTE["primary"],
            hover_color=PALETTE["primary_hover"],
            height=36,
            corner_radius=CARD_RADIUS,
            state="normal" if self.auth_service is not None else "disabled",
        )
        self.qr_login_button.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))

    def _build_runtime_panel(self) -> None:
        panel = ctk.CTkFrame(self.scroll_frame, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 12))
        panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            panel,
            text="下载与外观",
            anchor="w",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 6))

        form = ctk.CTkFrame(panel, fg_color=PALETTE["surface"], corner_radius=CARD_RADIUS)
        form.grid(row=1, column=0, sticky="ew", padx=18, pady=(8, 18))
        form.grid_columnconfigure(0, weight=1)

        self.ffmpeg_entry = self._entry_block(form, 0, "FFmpeg 路径", "默认直接使用 ffmpeg")
        ctk.CTkButton(
            form,
            text="选择 FFmpeg",
            command=self._pick_ffmpeg,
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
            height=36,
            corner_radius=CARD_RADIUS,
        ).grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 14))

        numeric_row = ctk.CTkFrame(form, fg_color="transparent")
        numeric_row.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 14))
        numeric_row.grid_columnconfigure((0, 1), weight=1)

        self.speed_limit_entry = self._mini_entry(numeric_row, 0, "限速 MB/s", "0 = 不限速")
        self.max_tasks_entry = self._mini_entry(numeric_row, 1, "并发任务", "建议 2 - 4")

        ctk.CTkLabel(
            form,
            text="界面风格",
            text_color=PALETTE["muted"],
            anchor="w",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        ).grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 6))

        self.appearance_option = ctk.CTkSegmentedButton(
            form,
            values=list(APPEARANCE_VALUES.keys()),
            command=lambda _value: self._on_form_changed(),
            height=36,
            corner_radius=CARD_RADIUS,
            fg_color=PALETTE["surface_alt"],
            selected_color=PALETTE["primary"],
            selected_hover_color=PALETTE["primary_hover"],
            unselected_color=PALETTE["surface_alt"],
            unselected_hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
        )
        self.appearance_option.grid(row=5, column=0, sticky="ew", padx=14, pady=(0, 14))

    def _build_status_panel(self) -> None:
        info_panel = ctk.CTkFrame(self.scroll_frame, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        info_panel.grid(row=1, column=0, columnspan=2, sticky="ew")
        info_panel.grid_columnconfigure((0, 1), weight=1)

        self.ffmpeg_status_label = ctk.CTkLabel(
            info_panel,
            text="",
            anchor="w",
            justify="left",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.ffmpeg_status_label.grid(row=0, column=0, sticky="ew", padx=18, pady=16)

        self.limit_status_label = ctk.CTkLabel(
            info_panel,
            text="",
            anchor="w",
            justify="left",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.limit_status_label.grid(row=0, column=1, sticky="ew", padx=18, pady=16)

    def _build_action_bar(self) -> None:
        self.action_bar = ctk.CTkFrame(self, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        self.action_bar.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.action_bar.grid_columnconfigure(0, weight=1)

        self.save_result_label = ctk.CTkLabel(
            self.action_bar,
            text="修改完成后保存设置",
            anchor="w",
            justify="left",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.save_result_label.grid(row=0, column=0, sticky="w", padx=18, pady=14)

        self.save_button = ctk.CTkButton(
            self.action_bar,
            text="保存全局设置",
            command=self._save_settings,
            fg_color=PALETTE["primary"],
            hover_color=PALETTE["primary_hover"],
            height=40,
            width=142,
            corner_radius=CARD_RADIUS,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
        )
        self.save_button.grid(row=0, column=1, sticky="e", padx=18, pady=12)

    def _bind_form_events(self) -> None:
        for entry in (
            self.sessdata_entry,
            self.save_path_entry,
            self.ffmpeg_entry,
            self.speed_limit_entry,
            self.max_tasks_entry,
        ):
            entry.bind("<KeyRelease>", lambda _event: self._on_form_changed())
            entry.bind("<FocusOut>", lambda _event: self._on_form_changed())
            entry.bind("<<Paste>>", lambda _event: self.after(50, self._on_form_changed))

    def _entry_block(self, master, row: int, label: str, placeholder: str, show: str | None = None):
        ctk.CTkLabel(
            master,
            text=label,
            text_color=PALETTE["muted"],
            anchor="w",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        ).grid(row=row * 2, column=0, sticky="ew", padx=14, pady=(14 if row == 0 else 0, 6))
        entry = ctk.CTkEntry(
            master,
            placeholder_text=placeholder,
            show=show or "",
            height=40,
            fg_color=PALETTE["input_bg"],
            border_color=PALETTE["border"],
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
        )
        entry.grid(row=row * 2 + 1, column=0, sticky="ew", padx=14, pady=(0, 12))
        return entry

    def _mini_entry(self, master, column: int, label: str, placeholder: str):
        wrapper = ctk.CTkFrame(master, fg_color="transparent")
        wrapper.grid(row=0, column=column, sticky="ew", padx=(0, 6) if column == 0 else (6, 0))
        wrapper.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            wrapper,
            text=label,
            text_color=PALETTE["muted"],
            anchor="w",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        entry = ctk.CTkEntry(
            wrapper,
            placeholder_text=placeholder,
            height=40,
            fg_color=PALETTE["input_bg"],
            border_color=PALETTE["border"],
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
        )
        entry.grid(row=1, column=0, sticky="ew")
        return entry

    def load_config(self, config: AppConfig) -> None:
        for entry, value in (
            (self.sessdata_entry, config.sessdata),
            (self.save_path_entry, config.save_path),
            (self.ffmpeg_entry, config.ffmpeg_path),
            (self.speed_limit_entry, str(config.speed_limit_mb)),
            (self.max_tasks_entry, str(config.max_concurrent_tasks)),
        ):
            entry.delete(0, "end")
            entry.insert(0, value)

        self.appearance_option.set(APPEARANCE_LABELS.get(config.appearance_mode or "system", "跟随系统"))
        self.limit_status_label.configure(
            text=f"当前总限速：{config.speed_limit_mb or 0:.2f} MB/s\n当前并发任务：{config.max_concurrent_tasks}",
            text_color=PALETTE["muted"],
        )
        self._loaded_form_values = self._current_form_values()
        self._update_ffmpeg_status()
        self._update_auth_status(config.sessdata)
        self._update_form_feedback()

    def _current_form_values(self) -> dict[str, str]:
        appearance_label = self.appearance_option.get().strip()
        return {
            "sessdata": self.sessdata_entry.get().strip(),
            "save_path": self.save_path_entry.get().strip(),
            "ffmpeg_path": self.ffmpeg_entry.get().strip(),
            "speed_limit_mb": self.speed_limit_entry.get().strip(),
            "max_concurrent_tasks": self.max_tasks_entry.get().strip(),
            "appearance_mode": APPEARANCE_VALUES.get(appearance_label, "system"),
        }

    def _has_unsaved_changes(self) -> bool:
        return self._current_form_values() != self._loaded_form_values

    def has_unsaved_changes(self) -> bool:
        return self._has_unsaved_changes()

    def _on_form_changed(self) -> None:
        self._update_ffmpeg_status()
        self._update_form_feedback()

    def _update_form_feedback(self) -> None:
        error_message = self._validate_form_fields()
        if error_message:
            self.save_button.configure(state="disabled")
            self.save_result_label.configure(text=error_message, text_color=PALETTE["error"])
            return

        self.save_button.configure(state="normal")
        if self._has_unsaved_changes():
            self.save_result_label.configure(text="有未保存修改", text_color=PALETTE["primary"])
        else:
            self.save_result_label.configure(text="修改完成后保存设置", text_color=PALETTE["muted"])

    def _validate_form_fields(self) -> str | None:
        speed_text = self.speed_limit_entry.get().strip()
        max_tasks_text = self.max_tasks_entry.get().strip()

        speed_valid = True
        max_tasks_valid = True
        error_message: str | None = None

        try:
            speed_value = float(speed_text or 0)
            if speed_value < 0:
                raise ValueError
        except ValueError:
            speed_valid = False
            error_message = "限速必须是大于等于 0 的数字"

        try:
            max_tasks = int(max_tasks_text or 1)
            if max_tasks < 1:
                raise ValueError
        except ValueError:
            max_tasks_valid = False
            error_message = error_message or "并发任务必须是大于等于 1 的整数"

        self._set_entry_valid(self.speed_limit_entry, speed_valid)
        self._set_entry_valid(self.max_tasks_entry, max_tasks_valid)
        return error_message

    def _set_entry_valid(self, entry: ctk.CTkEntry, is_valid: bool) -> None:
        entry.configure(border_color=PALETTE["border"] if is_valid else PALETTE["error"])

    def _collect_form_config(self) -> AppConfig:
        error_message = self._validate_form_fields()
        if error_message:
            raise ValueError(error_message)

        save_path = self.save_path_entry.get().strip() or str(Path.cwd() / "video")
        try:
            Path(save_path).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"保存目录不可用：{exc}") from exc

        ffmpeg_path = self.ffmpeg_entry.get().strip() or "ffmpeg"
        speed_limit = float(self.speed_limit_entry.get().strip() or 0)
        max_tasks = int(self.max_tasks_entry.get().strip() or 1)

        return AppConfig(
            sessdata=self.sessdata_entry.get().strip(),
            save_path=save_path,
            speed_limit_mb=speed_limit,
            max_concurrent_tasks=max_tasks,
            ffmpeg_path=ffmpeg_path,
            appearance_mode=APPEARANCE_VALUES.get(self.appearance_option.get().strip(), "system"),
        )

    def _save_settings(self) -> None:
        try:
            config = self._collect_form_config()
        except ValueError as exc:
            messagebox.showerror("设置错误", str(exc))
            return

        self.on_save_config(config)
        self._mark_config_saved(config)
        self.save_result_label.configure(text="全局设置已保存", text_color=PALETTE["success"])

    def _mark_config_saved(self, config: AppConfig) -> None:
        self._loaded_form_values = self._current_form_values()
        self.limit_status_label.configure(
            text=f"当前总限速：{config.speed_limit_mb or 0:.2f} MB/s\n当前并发任务：{config.max_concurrent_tasks}",
            text_color=PALETTE["muted"],
        )
        self._update_auth_status(config.sessdata)
        self._update_form_feedback()

    def _open_qr_login(self) -> None:
        if self.auth_service is None:
            messagebox.showwarning("扫码登录不可用", "当前没有初始化登录服务。")
            return
        QrLoginDialog(self, auth_service=self.auth_service, on_success=self._on_auth_success)

    def _on_auth_success(self, session: AuthSession) -> None:
        self.sessdata_entry.delete(0, "end")
        self.sessdata_entry.insert(0, session.sessdata)
        self._on_form_changed()
        self._save_settings()
        self.auth_status_label.configure(text="扫码登录成功，SESSDATA 已保存", text_color=PALETTE["success"])

    def _update_auth_status(self, sessdata: str) -> None:
        if not hasattr(self, "auth_status_label"):
            return
        if sessdata.strip():
            self.auth_status_label.configure(text="当前已保存 SESSDATA，可通过扫码登录更新", text_color=PALETTE["success"])
        else:
            self.auth_status_label.configure(text="未保存 SESSDATA，建议扫码登录", text_color=PALETTE["warning"])

    def _pick_save_dir(self) -> None:
        selected = filedialog.askdirectory(title="选择下载目录")
        if selected:
            self.save_path_entry.delete(0, "end")
            self.save_path_entry.insert(0, selected)
            self._on_form_changed()

    def _pick_ffmpeg(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择 ffmpeg.exe",
            filetypes=[("ffmpeg", "ffmpeg.exe"), ("可执行文件", "*.exe"), ("所有文件", "*.*")],
        )
        if selected:
            self.ffmpeg_entry.delete(0, "end")
            self.ffmpeg_entry.insert(0, selected)
            self._on_form_changed()

    def _update_ffmpeg_status(self) -> None:
        preview = self.ffmpeg_entry.get().strip() or "ffmpeg"
        resolved = FFmpegService.resolve_command(preview)
        exists = bool(shutil.which(resolved) or Path(resolved).exists())
        if exists:
            label = "内置 FFmpeg 可用" if resolved != preview and preview.lower() in {"ffmpeg", "ffmpeg.exe"} else "FFmpeg 可用"
            self.ffmpeg_status_label.configure(text=f"{label}\n{resolved}", text_color=PALETTE["success"])
        else:
            self.ffmpeg_status_label.configure(text=f"未检测到 FFmpeg\n{preview}", text_color=PALETTE["error"])
