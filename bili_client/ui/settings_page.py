from __future__ import annotations

import math
import time
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from bili_client.core.models import AppConfig
from bili_client.services.auth_service import AuthService, AuthSession
from bili_client.services.ffmpeg import FFmpegService
from bili_client.ui.async_tasks import UiTaskRunner
from bili_client.ui.styles import (
    BUTTON_HEIGHT,
    CARD_RADIUS,
    FONT_FAMILY,
    PALETTE,
    PANEL_RADIUS,
    STATE_COLORS,
    button_style,
)
from bili_client.ui.widgets import MotionButton, TextEntry, TextLabel, notify

APPEARANCE_LABELS = {
    "system": "跟随系统",
    "light": "浅色",
    "dark": "深色",
}
APPEARANCE_VALUES = {label: value for value, label in APPEARANCE_LABELS.items()}


class SettingsPage(ctk.CTkFrame):
    def __init__(self, master, on_save_config, auth_service: AuthService | None = None) -> None:
        super().__init__(master, fg_color=PALETTE["app_bg"])
        self.on_save_config = on_save_config
        self.auth_service = auth_service
        self._loaded_form_values: dict[str, str] = {}
        self._last_ffmpeg_preview = None
        self.show_sessdata_var = ctk.BooleanVar(value=False)
        self.organize_by_source_var = ctk.BooleanVar(value=False)
        self.cleanup_temp_files_var = ctk.BooleanVar(value=True)
        self.steady_mode_var = ctk.BooleanVar(value=True)
        self.animations_enabled_var = ctk.BooleanVar(value=True)
        self.ui_tasks = UiTaskRunner(self)

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

        TextLabel(
            panel,
            text="账号与保存",
            anchor="w",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 6))

        form = ctk.CTkFrame(panel, fg_color=PALETTE["surface"], corner_radius=CARD_RADIUS)
        form.grid(row=1, column=0, sticky="ew", padx=18, pady=(8, 18))
        form.grid_columnconfigure(0, weight=1)

        auth_card = ctk.CTkFrame(form, fg_color=PALETTE["surface_alt"], corner_radius=CARD_RADIUS)
        auth_card.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 12))
        auth_card.grid_columnconfigure(0, weight=1)

        self.account_state_label = TextLabel(
            auth_card,
            text="账号状态",
            anchor="w",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
        )
        self.account_state_label.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 2))

        self.account_detail_label = TextLabel(
            auth_card,
            text="正在读取登录状态",
            anchor="w",
            justify="left",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.account_detail_label.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))

        auth_actions = ctk.CTkFrame(auth_card, fg_color="transparent")
        auth_actions.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 14))
        auth_actions.grid_columnconfigure((0, 1), weight=1)

        self.check_auth_button = MotionButton(
            auth_actions,
            text="重新检查",
            command=self._check_auth_status,
            **button_style("secondary"),
            height=BUTTON_HEIGHT,
            corner_radius=CARD_RADIUS,
            state="normal" if self.auth_service is not None else "disabled",
        )
        self.check_auth_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.qr_login_button = MotionButton(
            auth_actions,
            text="扫码登录",
            command=self._open_qr_login,
            **button_style("primary"),
            height=BUTTON_HEIGHT,
            corner_radius=CARD_RADIUS,
            state="normal" if self.auth_service is not None else "disabled",
        )
        self.qr_login_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self.toggle_sessdata_button = MotionButton(
            form,
            text="显示 SESSDATA 高级项",
            command=self._toggle_sessdata_visibility,
            **button_style("secondary"),
            height=BUTTON_HEIGHT,
            corner_radius=CARD_RADIUS,
        )
        self.toggle_sessdata_button.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))

        self.sessdata_advanced_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.sessdata_advanced_frame.grid_columnconfigure(0, weight=1)
        self.sessdata_entry = self._entry_block(
            self.sessdata_advanced_frame,
            0,
            "SESSDATA",
            "通常无需手动填写，扫码登录会自动维护",
            show="*",
        )

        self.save_path_entry = self._entry_block(form, 2, "保存目录", "默认保存到 video")

        MotionButton(
            form,
            text="选择保存目录",
            command=self._pick_save_dir,
            **button_style("secondary"),
            height=BUTTON_HEIGHT,
            corner_radius=CARD_RADIUS,
        ).grid(row=6, column=0, sticky="ew", padx=14, pady=(0, 14))

    def _build_runtime_panel(self) -> None:
        panel = ctk.CTkFrame(self.scroll_frame, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 12))
        panel.grid_columnconfigure(0, weight=1)

        TextLabel(
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
        MotionButton(
            form,
            text="选择 FFmpeg",
            command=self._pick_ffmpeg,
            **button_style("secondary"),
            height=BUTTON_HEIGHT,
            corner_radius=CARD_RADIUS,
        ).grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 14))

        numeric_row = ctk.CTkFrame(form, fg_color="transparent")
        numeric_row.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 14))
        numeric_row.grid_columnconfigure((0, 1), weight=1)

        self.speed_limit_entry = self._mini_entry(numeric_row, 0, "限速 MB/s", "0 = 不限速")
        self.max_tasks_entry = self._mini_entry(numeric_row, 1, "并发任务", "建议 2 - 4")

        ctk.CTkCheckBox(
            form,
            text="稳妥模式（推荐）",
            variable=self.steady_mode_var,
            command=self._on_form_changed,
            fg_color=PALETTE["primary"],
            hover_color=PALETTE["primary_hover"],
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=4, column=0, sticky="w", padx=14, pady=(0, 14))

        TextLabel(
            form,
            text="界面风格",
            text_color=PALETTE["muted"],
            anchor="w",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        ).grid(row=5, column=0, sticky="ew", padx=14, pady=(0, 6))

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
        self.appearance_option.grid(row=6, column=0, sticky="ew", padx=14, pady=(0, 14))

        ctk.CTkSwitch(
            form,
            text="界面动画与平滑进度",
            variable=self.animations_enabled_var,
            command=self._on_form_changed,
            progress_color=PALETTE["primary"],
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=9, column=0, sticky="w", padx=14, pady=(0, 14))

        TextLabel(
            form,
            text="下载目录与命名",
            text_color=PALETTE["muted"],
            anchor="w",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        ).grid(row=7, column=0, sticky="ew", padx=14, pady=(0, 6))

        rules = ctk.CTkFrame(form, fg_color=PALETTE["surface_alt"], corner_radius=CARD_RADIUS)
        rules.grid(row=8, column=0, sticky="ew", padx=14, pady=(0, 14))
        rules.grid_columnconfigure(0, weight=1)

        ctk.CTkCheckBox(
            rules,
            text="按 UP 主 / 番剧名创建子文件夹",
            variable=self.organize_by_source_var,
            command=self._on_form_changed,
            fg_color=PALETTE["primary"],
            hover_color=PALETTE["primary_hover"],
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 8))

        TextLabel(
            rules,
            text="文件名模板",
            text_color=PALETTE["muted"],
            anchor="w",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        ).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
        self.filename_template_entry = TextEntry(
            rules,
            placeholder_text="{title}",
            height=38,
            fg_color=PALETTE["input_bg"],
            border_color=PALETTE["border"],
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
        )
        self.filename_template_entry.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
        TextLabel(
            rules,
            text="可用：{title} {media} {quality} {bvid} {cid} {index} {total}",
            text_color=PALETTE["muted"],
            anchor="w",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
        ).grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 10))

        ctk.CTkCheckBox(
            rules,
            text="下载结束后自动清理临时文件",
            variable=self.cleanup_temp_files_var,
            command=self._on_form_changed,
            fg_color=PALETTE["primary"],
            hover_color=PALETTE["primary_hover"],
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=4, column=0, sticky="w", padx=12, pady=(0, 12))

    def _build_status_panel(self) -> None:
        info_panel = ctk.CTkFrame(self.scroll_frame, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        info_panel.grid(row=1, column=0, columnspan=2, sticky="ew")
        info_panel.grid_columnconfigure((0, 1), weight=1)

        self.ffmpeg_status_label = TextLabel(
            info_panel,
            text="",
            anchor="w",
            justify="left",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.ffmpeg_status_label.grid(row=0, column=0, sticky="ew", padx=18, pady=16)

        self.limit_status_label = TextLabel(
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

        self.save_result_label = TextLabel(
            self.action_bar,
            text="修改完成后保存设置",
            anchor="w",
            justify="left",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.save_result_label.grid(row=0, column=0, sticky="w", padx=18, pady=14)

        self.save_button = MotionButton(
            self.action_bar,
            text="保存全局设置",
            command=self._save_settings,
            **button_style("primary"),
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
            self.filename_template_entry,
        ):
            entry.bind("<KeyRelease>", lambda _event: self._on_form_changed())
            entry.bind("<FocusOut>", lambda _event: self._on_form_changed())
            entry.bind("<<Paste>>", lambda _event: self.after(50, self._on_form_changed))

    def _entry_block(self, master, row: int, label: str, placeholder: str, show: str | None = None):
        TextLabel(
            master,
            text=label,
            text_color=PALETTE["muted"],
            anchor="w",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        ).grid(row=row * 2, column=0, sticky="ew", padx=14, pady=(14 if row == 0 else 0, 6))
        entry = TextEntry(
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
        TextLabel(
            wrapper,
            text=label,
            text_color=PALETTE["muted"],
            anchor="w",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        entry = TextEntry(
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
            (self.filename_template_entry, config.filename_template),
        ):
            entry.delete(0, "end")
            entry.insert(0, value)

        self.organize_by_source_var.set(bool(config.organize_by_source))
        self.cleanup_temp_files_var.set(bool(config.cleanup_temp_files))
        self.steady_mode_var.set(bool(config.steady_mode))
        self.animations_enabled_var.set(bool(config.animations_enabled))
        self.appearance_option.set(APPEARANCE_LABELS.get(config.appearance_mode or "system", "跟随系统"))
        self.limit_status_label.configure(
            text=(
                f"当前总限速：{config.speed_limit_mb or 0:.2f} MB/s\n"
                f"当前并发任务：{min(config.max_concurrent_tasks, 2) if config.steady_mode else config.max_concurrent_tasks}\n"
                f"请求策略：{'稳妥模式' if config.steady_mode else '标准模式'}\n"
                f"命名模板：{config.filename_template or '{title}'}"
            ),
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
            "organize_by_source": str(bool(self.organize_by_source_var.get())),
            "filename_template": self.filename_template_entry.get().strip(),
            "cleanup_temp_files": str(bool(self.cleanup_temp_files_var.get())),
            "steady_mode": str(bool(self.steady_mode_var.get())),
            "ffmpeg_path": self.ffmpeg_entry.get().strip(),
            "speed_limit_mb": self.speed_limit_entry.get().strip(),
            "max_concurrent_tasks": self.max_tasks_entry.get().strip(),
            "appearance_mode": APPEARANCE_VALUES.get(appearance_label, "system"),
            "animations_enabled": str(bool(self.animations_enabled_var.get())),
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
            if speed_value < 0 or not math.isfinite(speed_value):
                raise ValueError
        except ValueError:
            speed_valid = False
            error_message = "限速必须是大于等于 0 的数字"

        try:
            max_tasks = int(max_tasks_text or 1)
            if max_tasks < 1 or max_tasks > 16:
                raise ValueError
        except ValueError:
            max_tasks_valid = False
            error_message = error_message or "并发任务必须是 1 到 16 之间的整数"

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

        ffmpeg_path = self.ffmpeg_entry.get().strip() or "ffmpeg"
        speed_limit = float(self.speed_limit_entry.get().strip() or 0)
        max_tasks = int(self.max_tasks_entry.get().strip() or 1)

        return AppConfig(
            sessdata=self.sessdata_entry.get().strip(),
            save_path=save_path,
            organize_by_source=bool(self.organize_by_source_var.get()),
            filename_template=self.filename_template_entry.get().strip() or "{title}",
            cleanup_temp_files=bool(self.cleanup_temp_files_var.get()),
            steady_mode=bool(self.steady_mode_var.get()),
            speed_limit_mb=speed_limit,
            max_concurrent_tasks=max_tasks,
            ffmpeg_path=ffmpeg_path,
            appearance_mode=APPEARANCE_VALUES.get(self.appearance_option.get().strip(), "system"),
            animations_enabled=bool(self.animations_enabled_var.get()),
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
        notify(self, "设置已保存")

    def _mark_config_saved(self, config: AppConfig) -> None:
        self._loaded_form_values = self._current_form_values()
        self.limit_status_label.configure(
            text=(
                f"当前总限速：{config.speed_limit_mb or 0:.2f} MB/s\n"
                f"当前并发任务：{min(config.max_concurrent_tasks, 2) if config.steady_mode else config.max_concurrent_tasks}\n"
                f"请求策略：{'稳妥模式' if config.steady_mode else '标准模式'}\n"
                f"命名模板：{config.filename_template or '{title}'}"
            ),
            text_color=PALETTE["muted"],
        )
        self._update_auth_status(config.sessdata)
        self._update_form_feedback()

    def _toggle_sessdata_visibility(self) -> None:
        visible = not self.show_sessdata_var.get()
        self.show_sessdata_var.set(visible)
        if visible:
            self.sessdata_advanced_frame.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))
            self.toggle_sessdata_button.configure(text="隐藏 SESSDATA 高级项")
        else:
            self.sessdata_advanced_frame.grid_forget()
            self.toggle_sessdata_button.configure(text="显示 SESSDATA 高级项")

    def _open_qr_login(self) -> None:
        if self.auth_service is None:
            messagebox.showwarning("扫码登录不可用", "当前没有初始化登录服务。")
            return

        from bili_client.ui.auth_dialog import QrLoginDialog

        QrLoginDialog(self, auth_service=self.auth_service, on_success=self._on_auth_success)

    def _on_auth_success(self, session: AuthSession) -> None:
        self._apply_auth_session(session, save=True)
        self._set_account_status("已登录", self._auth_session_detail(session), STATE_COLORS["success"])

    def _check_auth_status(self) -> None:
        if self.auth_service is None:
            messagebox.showwarning("登录检查不可用", "当前没有初始化登录服务。")
            return

        self.check_auth_button.configure(state="disabled", text="检查中...")
        self._set_account_status("正在检查", "正在向 B 站确认登录状态", STATE_COLORS["busy"])
        sessdata = self.sessdata_entry.get().strip()
        self.ui_tasks.run(
            "auth-check",
            lambda: self._ensure_auth_session(sessdata),
            on_success=self._on_auth_check_success,
            on_error=lambda exc: self._on_auth_check_failure(str(exc)),
        )

    def _ensure_auth_session(self, sessdata: str) -> AuthSession | None:
        if self.auth_service is None:
            return None
        return self.auth_service.ensure_latest_session(sessdata, force=True)

    def _on_auth_check_success(self, session: AuthSession | None) -> None:
        self.check_auth_button.configure(state="normal", text="重新检查")
        if session is None or not session.sessdata:
            self._set_account_status("未登录", "没有检测到可用登录态，请扫码登录。", STATE_COLORS["warning"])
            return

        self._apply_auth_session(session, save=True)
        self._set_account_status("已登录", self._auth_session_detail(session), STATE_COLORS["success"])

    def _on_auth_check_failure(self, message: str) -> None:
        self.check_auth_button.configure(state="normal", text="重新检查")
        self._set_account_status("登录失效", message or "登录状态检查失败，请重新扫码登录。", STATE_COLORS["error"])

    def _apply_auth_session(self, session: AuthSession, save: bool) -> None:
        self.sessdata_entry.delete(0, "end")
        self.sessdata_entry.insert(0, session.sessdata)
        self._on_form_changed()
        if save:
            self._save_settings()

    def _update_auth_status(self, sessdata: str) -> None:
        if not hasattr(self, "account_state_label"):
            return
        session = self.auth_service.load_session() if self.auth_service is not None else None
        if session is not None and session.sessdata:
            self._set_account_status("已登录", self._auth_session_detail(session), STATE_COLORS["success"])
            return
        if sessdata.strip():
            self._set_account_status(
                "已保存登录态", "检测到旧版 SESSDATA，可点击重新检查确认是否可用。", STATE_COLORS["warning"]
            )
            return
        self._set_account_status("未登录", "扫码登录后会自动保存登录状态。", STATE_COLORS["warning"])

    def _set_account_status(self, title: str, detail: str, color) -> None:
        self.account_state_label.configure(text=title, text_color=color)
        self.account_detail_label.configure(text=detail, text_color=PALETTE["muted"])

    def _auth_session_detail(self, session: AuthSession) -> str:
        user_id = session.cookies.get("DedeUserID") or "未知 UID"
        updated = self._format_timestamp(session.updated_at)
        checked = self._format_timestamp(session.last_checked_at)
        return f"UID：{user_id}\n上次刷新：{updated}\n上次检查：{checked}"

    def _format_timestamp(self, timestamp: float | None) -> str:
        if not timestamp:
            return "尚未检查"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))

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
        if preview == self._last_ffmpeg_preview:
            return
        self._last_ffmpeg_preview = preview
        resolved = FFmpegService.resolve_command(preview)
        exists = FFmpegService(preview).is_available()
        if exists:
            self.ffmpeg_status_label.configure(text=f"FFmpeg 可用\n{resolved}", text_color=PALETTE["success"])
        else:
            self.ffmpeg_status_label.configure(
                text="未检测到 FFmpeg\n将 FFmpeg 的 bin 目录加入 PATH，或在这里选择 ffmpeg.exe。",
                text_color=PALETTE["error"],
            )
