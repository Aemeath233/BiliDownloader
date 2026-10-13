from __future__ import annotations

import hashlib
import time
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from bili_client.core.errors import humanize_error
from bili_client.core.models import AppConfig, MediaParseResult, QualityOption
from bili_client.core.naming import render_filename_template
from bili_client.services.bili_api import BilibiliApi
from bili_client.services.download_manager import DownloadManager, DownloadTaskSpec
from bili_client.ui.async_tasks import UiTaskRunner
from bili_client.ui.styles import CARD_RADIUS, FONT_FAMILY, PALETTE, PANEL_RADIUS, STATE_COLORS, button_style
from bili_client.ui.widgets import MotionButton, TextLabel, notify, wrap_to_panel


class EpisodeSelection:
    """Keep off-screen selections in Python; bind Tk variables only for visible rows."""

    def __init__(self, selected: bool = False) -> None:
        self._selected = selected
        self._variable = None

    def get(self) -> bool:
        return bool(self._variable.get()) if self._variable is not None else self._selected

    def set(self, selected: bool) -> None:
        self._selected = bool(selected)
        if self._variable is not None:
            self._variable.set(self._selected)

    def attach(self, master):
        self._variable = ctk.BooleanVar(master=master, value=self._selected)
        return self._variable

    def detach(self) -> None:
        self._selected = self.get()
        self._variable = None


class AddTaskPage(ctk.CTkFrame):
    def __init__(
        self,
        master,
        api: BilibiliApi,
        manager: DownloadManager,
        get_config,
        emit_log,
        show_queue_page,
        open_settings_page,
        refresh_auth_session=None,
    ) -> None:
        super().__init__(master, fg_color=PALETTE["app_bg"])
        self.api = api
        self.manager = manager
        self.get_config = get_config
        self.emit_log = emit_log
        self.show_queue_page = show_queue_page
        self.open_settings_page = open_settings_page
        self.refresh_auth_session = refresh_auth_session or (lambda: self.get_config().sessdata.strip())
        self.ui_tasks = UiTaskRunner(self)

        self.current_media: MediaParseResult | None = None
        self.current_qualities: list[QualityOption] = []
        self.episode_selection_vars: dict[int, EpisodeSelection] = {}
        self.episode_checkboxes: dict[int, ctk.CTkCheckBox] = {}
        self.episode_rows: dict[int, ctk.CTkFrame] = {}
        self.episode_page_size = 10
        self.current_episode_page = 0
        self.quality_cache: dict[int, list[QualityOption]] = {}
        self.active_episode_index: int | None = None
        self._quality_request_token = 0
        self._parse_request_token = 0
        self._is_parsing = False
        self._is_adding = False
        self._last_auto_parse_url = ""
        self._auto_parse_after_id = None
        self.preferred_quality_label: str | None = None
        self._quality_auth_scope = hashlib.sha256(self.get_config().sessdata.encode()).digest()

        self.parse_status_var = ctk.StringVar(value="等待链接")
        self.media_title_var = ctk.StringVar(value="尚未解析")
        self.media_info_var = ctk.StringVar(value="解析完成后会显示视频信息")
        self.selection_summary_var = ctk.StringVar(value="分集：等待解析")
        self.quality_hint_var = ctk.StringVar(value="画质：等待分集选择")
        self.quality_access_var = ctk.StringVar(value="选择分集后，仅显示当前账号实际可下载的画质")
        self.selection_detail_var = ctk.StringVar(value="未选择分集")
        self.pagination_info_var = ctk.StringVar(value="")
        self.filename_preview_var = ctk.StringVar(value="文件名：等待解析")

        self.grid_columnconfigure(0, weight=4, uniform="add_task_columns")
        self.grid_columnconfigure(1, weight=5, uniform="add_task_columns")
        self.grid_rowconfigure(0, weight=1)

        self._build_input_panel()
        self._build_selection_panel()
        self._bind_shortcuts()
        self._refresh_add_button_state()

    def _build_input_panel(self) -> None:
        panel = ctk.CTkFrame(self, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(4, weight=1)

        TextLabel(
            panel,
            text="链接解析",
            anchor="w",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 4))
        TextLabel(
            panel,
            text="支持 BV、ep、ss 链接，也可以直接粘贴分享文本",
            anchor="w",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=1, column=0, sticky="ew", padx=18)

        self.url_input = ctk.CTkTextbox(
            panel,
            height=96,
            fg_color=PALETTE["surface"],
            border_color=PALETTE["border"],
            border_width=1,
            corner_radius=CARD_RADIUS,
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
        )
        self.url_input.grid(row=2, column=0, sticky="ew", padx=18, pady=(14, 12))

        action_row = ctk.CTkFrame(panel, fg_color="transparent")
        action_row.grid(row=3, column=0, sticky="ew", padx=18)
        action_row.grid_columnconfigure(0, weight=1)

        self.parse_button = MotionButton(
            action_row,
            text="解析链接",
            command=self._parse_current_url,
            **button_style("primary"),
            height=40,
            corner_radius=CARD_RADIUS,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
        )
        self.parse_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.clear_button = MotionButton(
            action_row,
            text="清空",
            command=self._clear_input,
            **button_style("secondary"),
            width=86,
            height=40,
            corner_radius=CARD_RADIUS,
        )
        self.clear_button.grid(row=0, column=1, sticky="e")

        status_panel = ctk.CTkFrame(panel, fg_color=PALETTE["surface"], corner_radius=CARD_RADIUS)
        status_panel.grid(row=4, column=0, sticky="new", padx=18, pady=(18, 12))
        status_panel.grid_columnconfigure(0, weight=1)

        self.parse_status_label = TextLabel(
            status_panel,
            textvariable=self.parse_status_var,
            anchor="w",
            text_color=STATE_COLORS["idle"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        )
        self.parse_status_label.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 3))
        self.media_title_label = TextLabel(
            status_panel,
            textvariable=self.media_title_var,
            anchor="w",
            justify="left",
            wraplength=520,
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
        )
        self.media_title_label.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))
        self.media_info_label = TextLabel(
            status_panel,
            textvariable=self.media_info_var,
            anchor="w",
            justify="left",
            wraplength=520,
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.media_info_label.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))

        self.parse_progress = ctk.CTkProgressBar(
            status_panel,
            height=5,
            mode="indeterminate",
            corner_radius=4,
            progress_color=PALETTE["primary"],
        )

        config_panel = ctk.CTkFrame(panel, fg_color=PALETTE["surface_alt"], corner_radius=CARD_RADIUS)
        config_panel.grid(row=5, column=0, sticky="ew", padx=18, pady=(0, 18))
        config_panel.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(config_panel, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        header.grid_columnconfigure(0, weight=1)
        TextLabel(
            header,
            text="当前下载配置",
            anchor="w",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="ew")
        MotionButton(
            header,
            text="调整",
            command=self.open_settings_page,
            width=70,
            height=30,
            corner_radius=CARD_RADIUS,
            **button_style("secondary"),
        ).grid(row=0, column=1, sticky="e")

        self.config_summary_label = TextLabel(
            config_panel,
            text="--",
            anchor="w",
            justify="left",
            wraplength=520,
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.config_summary_label.grid(row=1, column=0, sticky="ew", padx=14, pady=(2, 14))
        wrap_to_panel(panel, [self.media_title_label, self.media_info_label, self.config_summary_label])

    def _build_selection_panel(self) -> None:
        panel = ctk.CTkFrame(self, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        panel.grid(row=0, column=1, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(4, weight=1)

        TextLabel(
            panel,
            text="分集与画质",
            anchor="w",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 4))
        TextLabel(
            panel,
            textvariable=self.selection_summary_var,
            anchor="w",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=1, column=0, sticky="ew", padx=18)
        self.selection_detail_label = TextLabel(
            panel,
            textvariable=self.selection_detail_var,
            anchor="w",
            justify="left",
            height=34,
            wraplength=520,
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.selection_detail_label.grid(row=2, column=0, sticky="ew", padx=18, pady=(3, 0))

        tools = ctk.CTkFrame(panel, fg_color="transparent")
        tools.grid(row=3, column=0, sticky="ew", padx=18, pady=(14, 10))
        tools.grid_columnconfigure((0, 1, 2), weight=1)
        actions = (
            ("全选", self._select_all_episodes),
            ("反选", self._invert_episode_selection),
            ("清空选择", self._clear_episode_selection),
        )
        for column, (label, handler) in enumerate(actions):
            MotionButton(
                tools,
                text=label,
                command=handler,
                height=32,
                corner_radius=CARD_RADIUS,
                **button_style("secondary"),
            ).grid(row=0, column=column, sticky="ew", padx=(0, 6) if column < 2 else 0)

        self.episode_scroll = ctk.CTkScrollableFrame(
            panel,
            fg_color=PALETTE["surface"],
            corner_radius=CARD_RADIUS,
        )
        self.episode_scroll.grid(row=4, column=0, sticky="nsew", padx=18, pady=(0, 12))
        self.episode_scroll.grid_columnconfigure(0, weight=1)

        self.episode_placeholder = TextLabel(
            self.episode_scroll,
            text="解析完成后会显示可下载分集",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
        )
        self.episode_placeholder.grid(row=0, column=0, pady=28)

        self.pagination_bar = ctk.CTkFrame(panel, fg_color="transparent")
        self.pagination_bar.grid(row=5, column=0, sticky="ew", padx=18, pady=(0, 12))
        self.pagination_bar.grid_columnconfigure(1, weight=1)
        self.pagination_bar.grid_remove()

        quality_panel = ctk.CTkFrame(panel, fg_color=PALETTE["surface_alt"], corner_radius=CARD_RADIUS)
        quality_panel.grid(row=6, column=0, sticky="ew", padx=18, pady=(0, 18))
        quality_panel.grid_columnconfigure(0, weight=1)

        TextLabel(
            quality_panel,
            textvariable=self.quality_hint_var,
            anchor="w",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))

        self.quality_combo = ctk.CTkOptionMenu(
            quality_panel,
            values=["等待画质"],
            command=self._on_quality_selected,
            state="disabled",
            height=36,
            dynamic_resizing=False,
            fg_color=PALETTE["input_bg"],
            button_color=PALETTE["secondary"],
            button_hover_color=PALETTE["secondary_hover"],
            dropdown_fg_color=PALETTE["panel_bg"],
            dropdown_hover_color=PALETTE["surface"],
            dropdown_text_color=PALETTE["text"],
            text_color=PALETTE["text"],
            anchor="w",
        )
        self.quality_combo.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))
        self.quality_combo.set("等待画质")

        self.quality_access_label = TextLabel(
            quality_panel,
            textvariable=self.quality_access_var,
            anchor="w",
            justify="left",
            wraplength=520,
            text_color=PALETTE["accent"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.quality_access_label.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 10))

        TextLabel(
            quality_panel,
            textvariable=self.filename_preview_var,
            anchor="w",
            justify="left",
            wraplength=540,
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 12))

        self.add_button = MotionButton(
            quality_panel,
            text="先解析链接",
            command=self._add_task_from_selection,
            **button_style("primary"),
            height=42,
            state="disabled",
            corner_radius=CARD_RADIUS,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
        )
        self.add_button.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 14))
        wrap_to_panel(quality_panel, [self.quality_access_label], padding=28)

    def _bind_shortcuts(self) -> None:
        self.url_input.bind("<Control-Return>", self._submit_parse_shortcut)
        self.url_input.bind("<<Paste>>", self._schedule_auto_parse)
        self.url_input.bind("<Control-v>", self._schedule_auto_parse)

    def _submit_parse_shortcut(self, _event=None):
        self._parse_current_url()
        return "break"

    def _schedule_auto_parse(self, _event=None):
        if self._auto_parse_after_id is not None:
            self.after_cancel(self._auto_parse_after_id)
        self._auto_parse_after_id = self.after(120, self._auto_parse_if_possible)

    def _auto_parse_if_possible(self) -> None:
        self._auto_parse_after_id = None
        if self._is_parsing:
            return

        content = self.url_input.get("1.0", "end").strip()
        url = self.api.extract_url(content)
        if not url or url == self._last_auto_parse_url:
            return

        self._last_auto_parse_url = url
        self._parse_current_url()

    def _clear_input(self) -> None:
        self._last_auto_parse_url = ""
        self.url_input.delete("1.0", "end")
        self._quality_request_token = time.time_ns()
        self.current_media = None
        self.current_qualities = []
        self.quality_cache.clear()
        self.preferred_quality_label = None
        self._set_parse_state("idle", "等待链接", "尚未解析", "解析完成后会显示视频信息")
        self._reset_episode_selector("解析完成后会显示可下载分集")
        self._set_quality_waiting_state("等待画质")

    def _on_quality_selected(self, value: str) -> None:
        if any(item.label == value for item in self.current_qualities):
            self.preferred_quality_label = value
        self._update_filename_preview()
        self._refresh_add_button_state()

    def _apply_quality_options(self, labels: list[str]) -> None:
        preferred = self.preferred_quality_label if self.preferred_quality_label in labels else labels[0]
        self.quality_combo.configure(values=labels, state="normal")
        self.quality_combo.set(preferred)
        self._on_quality_selected(preferred)
        if self.current_qualities:
            self.quality_access_var.set(
                self.current_qualities[0].availability_note
                or "仅显示当前账号实际可下载的画质；下载前会再次校验，不会自动降档。"
            )

    def update_config_preview(self, config: AppConfig) -> None:
        scope = hashlib.sha256(config.sessdata.encode()).digest()
        if scope != self._quality_auth_scope:
            self._quality_auth_scope = scope
            self.quality_cache.clear()
            self.preferred_quality_label = None
            self._set_quality_waiting_state("正在更新账号可下载画质")
            if self.current_media is not None and self.active_episode_index is not None:
                self._load_qualities_for_episode(self.active_episode_index)
        sessdata_text = "已设置" if config.sessdata.strip() else "未设置"
        speed_limit = f"{config.speed_limit_mb:.2f} MB/s" if config.speed_limit_mb > 0 else "不限速"
        strategy = "稳妥" if config.steady_mode else "标准"
        effective_tasks = min(config.max_concurrent_tasks, 2) if config.steady_mode else config.max_concurrent_tasks
        self.config_summary_label.configure(
            text=(
                f"保存目录：{config.save_path}\n"
                f"SESSDATA：{sessdata_text}  |  并发：{effective_tasks}  |  限速：{speed_limit}  |  {strategy}\n"
                f"命名：{config.filename_template or '{title}'}"
                f"{'  |  按来源分文件夹' if config.organize_by_source else ''}\n"
                f"FFmpeg：{config.ffmpeg_path}"
            )
        )
        self._update_filename_preview()

    def _reset_episode_selector(self, placeholder: str) -> None:
        for index in self.episode_rows:
            self.episode_selection_vars[index].detach()
        for row in self.episode_rows.values():
            row.destroy()
        self.episode_rows.clear()
        self.episode_checkboxes.clear()
        self.episode_selection_vars.clear()
        self.current_episode_page = 0
        self.active_episode_index = None

        self.episode_placeholder.configure(text=placeholder)
        self.episode_placeholder.grid(row=0, column=0, pady=28)
        self._render_episode_pagination()
        self.selection_summary_var.set("分集：等待解析")
        self.selection_detail_var.set("未选择分集")
        self.quality_hint_var.set("画质：等待分集选择")
        self.filename_preview_var.set("文件名：等待解析")
        self._refresh_add_button_state()

    def _populate_episode_selector(self, media: MediaParseResult) -> None:
        self._reset_episode_selector("请选择要加入队列的分集")
        self.quality_cache.clear()

        for index, _episode in enumerate(media.episodes):
            variable = EpisodeSelection(selected=index == media.default_episode_index)
            self.episode_selection_vars[index] = variable

        self.current_episode_page = media.default_episode_index // self.episode_page_size
        self._render_episode_page(media)
        self.active_episode_index = media.default_episode_index
        self._update_episode_selection_summary()
        self.after(0, self._scroll_episode_list_to_top)

    def _render_episode_page(self, media: MediaParseResult | None = None) -> None:
        media = media or self.current_media
        if not media:
            return

        for index in self.episode_rows:
            self.episode_selection_vars[index].detach()
        for row in self.episode_rows.values():
            row.destroy()
        self.episode_rows.clear()
        self.episode_checkboxes.clear()
        self.episode_placeholder.grid_forget()

        total = len(media.episodes)
        page_count = self._episode_page_count(total)
        self.current_episode_page = max(0, min(self.current_episode_page, page_count - 1))
        start, end = self._episode_page_range(total)

        for row_index, index in enumerate(range(start, end)):
            episode = media.episodes[index]
            variable = self.episode_selection_vars[index]
            row = ctk.CTkFrame(self.episode_scroll, fg_color="transparent", height=38)
            row.grid(row=row_index, column=0, sticky="ew", padx=8, pady=(5 if row_index == 0 else 2, 2))
            row.grid_columnconfigure(1, weight=1)
            row.grid_propagate(False)

            checkbox = ctk.CTkCheckBox(
                row,
                text="",
                variable=variable.attach(row),
                onvalue=True,
                offvalue=False,
                command=lambda idx=index: self._on_episode_checkbox_changed(idx),
                fg_color=PALETTE["primary"],
                hover_color=PALETTE["primary_hover"],
                text_color=PALETTE["text"],
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                width=30,
                height=28,
            )
            checkbox.grid(row=0, column=0, sticky="w", padx=(0, 6), pady=5)

            label = TextLabel(
                row,
                text=self._episode_display_title(index, episode.title, total),
                anchor="w",
                text_color=PALETTE["text"],
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            )
            label.grid(row=0, column=1, sticky="ew", pady=5)
            label.bind("<Button-1>", lambda _event, idx=index: self._toggle_episode_from_row(idx))

            self.episode_checkboxes[index] = checkbox
            self.episode_rows[index] = row

        self._render_episode_pagination()

    def _episode_page_count(self, total: int | None = None) -> int:
        if total is None:
            total = len(self.episode_selection_vars)
        return max(1, (total + self.episode_page_size - 1) // self.episode_page_size)

    def _episode_page_range(self, total: int | None = None) -> tuple[int, int]:
        if total is None:
            total = len(self.episode_selection_vars)
        start = self.current_episode_page * self.episode_page_size
        return start, min(start + self.episode_page_size, total)

    def _render_episode_pagination(self) -> None:
        if not hasattr(self, "pagination_bar"):
            return

        for child in self.pagination_bar.winfo_children():
            child.destroy()

        total = len(self.episode_selection_vars)
        if total <= self.episode_page_size:
            self.pagination_bar.grid_remove()
            self.pagination_info_var.set("")
            return

        page_count = self._episode_page_count(total)
        self.current_episode_page = max(0, min(self.current_episode_page, page_count - 1))
        start, end = self._episode_page_range(total)
        self.pagination_info_var.set(
            f"第 {self.current_episode_page + 1} / {page_count} 页 · {start + 1}-{end} / {total}"
        )

        self.pagination_bar.grid()
        MotionButton(
            self.pagination_bar,
            text="上一页",
            command=lambda: self._change_episode_page(self.current_episode_page - 1),
            width=72,
            height=30,
            state="normal" if self.current_episode_page > 0 else "disabled",
            corner_radius=CARD_RADIUS,
            **button_style("secondary"),
        ).grid(row=0, column=0, sticky="w")

        page_strip = ctk.CTkFrame(self.pagination_bar, fg_color="transparent")
        page_strip.grid(row=0, column=1)

        for offset, page_index in enumerate(self._visible_episode_pages(page_count)):
            if page_index is None:
                TextLabel(
                    page_strip,
                    text="...",
                    width=24,
                    text_color=PALETTE["muted"],
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                ).grid(row=0, column=offset, padx=2)
                continue

            is_current = page_index == self.current_episode_page
            MotionButton(
                page_strip,
                text=str(page_index + 1),
                command=lambda page=page_index: self._change_episode_page(page),
                width=34,
                height=30,
                corner_radius=CARD_RADIUS,
                **button_style("primary" if is_current else "secondary"),
            ).grid(row=0, column=offset, padx=2)

        MotionButton(
            self.pagination_bar,
            text="下一页",
            command=lambda: self._change_episode_page(self.current_episode_page + 1),
            width=72,
            height=30,
            state="normal" if self.current_episode_page < page_count - 1 else "disabled",
            corner_radius=CARD_RADIUS,
            **button_style("secondary"),
        ).grid(row=0, column=2, sticky="e", padx=(8, 0))

        TextLabel(
            self.pagination_bar,
            textvariable=self.pagination_info_var,
            anchor="e",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=0, column=3, sticky="e", padx=(10, 0))

    def _visible_episode_pages(self, page_count: int) -> list[int | None]:
        if page_count <= 7:
            return list(range(page_count))

        current = self.current_episode_page
        pages: list[int | None] = [0]
        window_start = max(1, current - 1)
        window_end = min(page_count - 2, current + 1)

        if window_start > 1:
            pages.append(None)
        pages.extend(range(window_start, window_end + 1))
        if window_end < page_count - 2:
            pages.append(None)
        pages.append(page_count - 1)
        return pages

    def _change_episode_page(self, page: int) -> None:
        if not self.current_media:
            return
        page_count = self._episode_page_count(len(self.current_media.episodes))
        next_page = max(0, min(page, page_count - 1))
        if next_page == self.current_episode_page:
            return
        self.current_episode_page = next_page
        self._render_episode_page(self.current_media)
        self.after(0, self._scroll_episode_list_to_top)

    def _episode_display_title(self, index: int, title: str, total: int) -> str:
        if total == 1:
            return f"单集 · {self._compact_text(title, 54)}"
        return self._compact_text(title, 58)

    @staticmethod
    def _compact_text(text: str, limit: int) -> str:
        value = str(text or "").strip()
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 3)].rstrip() + "..."

    def _scroll_episode_list_to_top(self) -> None:
        canvas = getattr(self.episode_scroll, "_parent_canvas", None)
        if canvas is not None:
            canvas.yview_moveto(0)

    def _toggle_episode_from_row(self, index: int) -> None:
        variable = self.episode_selection_vars.get(index)
        if variable is None:
            return
        variable.set(not variable.get())
        self._on_episode_checkbox_changed(index)

    def _selected_episode_indices(self) -> list[int]:
        return [index for index, variable in self.episode_selection_vars.items() if variable.get()]

    def _selected_episode_preview(self, selected: list[int]) -> str:
        if not self.current_media or not selected:
            return "未选择分集"
        titles = [self._compact_text(self.current_media.episodes[index].title, 18) for index in selected[:3]]
        suffix = f" 等 {len(selected)} 个" if len(selected) > 3 else ""
        return "已选：" + "、".join(titles) + suffix

    def _update_episode_selection_summary(self) -> None:
        total = len(self.episode_selection_vars)
        selected = self._selected_episode_indices()
        active_title = "未选择"
        if (
            self.current_media
            and self.active_episode_index is not None
            and 0 <= self.active_episode_index < len(self.current_media.episodes)
        ):
            active_title = self._compact_text(self.current_media.episodes[self.active_episode_index].title, 34)
        self.selection_summary_var.set(f"分集：已选择 {len(selected)} / {total}")
        self.quality_hint_var.set(f"画质基准：{active_title}")
        self.selection_detail_var.set(self._selected_episode_preview(selected))
        self._update_filename_preview()
        self._refresh_add_button_state()

    def _set_quality_waiting_state(self, text: str) -> None:
        self._quality_request_token = time.time_ns()
        self.quality_combo.configure(values=[text], state="disabled")
        self.quality_combo.set(text)
        self.current_qualities = []
        self.quality_access_var.set("当前账号的可下载画质将在读取后显示，受限档位不会进入选项。")
        self._update_filename_preview()
        self._refresh_add_button_state()

    def _set_parse_state(self, state: str, status: str, title: str | None = None, info: str | None = None) -> None:
        self.parse_status_var.set(status)
        if title is not None:
            self.media_title_var.set(title)
        if info is not None:
            self.media_info_var.set(info)
        if hasattr(self, "parse_status_label"):
            self.parse_status_label.configure(text_color=STATE_COLORS.get(state, STATE_COLORS["idle"]))

    def _set_parse_busy(self, busy: bool) -> None:
        if busy:
            self.parse_button.configure(state="disabled", text="正在解析...")
            self.clear_button.configure(state="disabled")
            self.add_button.configure(state="disabled")
            self.parse_progress.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 12))
            self.parse_progress.start()
            return

        self.parse_progress.stop()
        self.parse_progress.grid_forget()
        self.parse_button.configure(state="normal", text="解析链接")
        self.clear_button.configure(state="normal")
        self._refresh_add_button_state()

    def _refresh_add_button_state(self) -> None:
        selected_count = len(self._selected_episode_indices())
        has_quality = bool(self.current_qualities)

        if self._is_adding:
            text = "正在加入下载队列..."
            state = "disabled"
        elif not self.current_media:
            text = "先解析链接"
            state = "disabled"
        elif not selected_count:
            text = "选择分集后可加入"
            state = "disabled"
        elif not has_quality:
            text = "读取画质后可加入"
            state = "disabled"
        else:
            text = f"加入下载队列（{selected_count}）"
            state = "normal"

        if hasattr(self, "add_button"):
            self.add_button.configure(text=text, state=state)

    def _selected_quality(self) -> QualityOption | None:
        if not self.current_qualities:
            return None
        quality_label = self.quality_combo.get().strip()
        return next((item for item in self.current_qualities if item.label == quality_label), self.current_qualities[0])

    def _filename_for_episode(self, episode_index: int, quality: QualityOption, selected_indices: list[int]) -> str:
        assert self.current_media is not None
        episode = self.current_media.episodes[episode_index]
        return render_filename_template(
            self.get_config().filename_template,
            media_title=self.current_media.title,
            episode=episode,
            quality=quality,
            index=selected_indices.index(episode_index) + 1,
            total=len(selected_indices),
        )

    def _target_save_dir(self, config: AppConfig) -> Path:
        save_dir = Path(config.save_path)
        if config.organize_by_source and self.current_media is not None:
            folder = self.current_media.folder_title or self.current_media.source_name or self.current_media.title
            save_dir = save_dir / folder
        return save_dir

    def _update_filename_preview(self) -> None:
        if not hasattr(self, "filename_preview_var"):
            return
        if not self.current_media:
            self.filename_preview_var.set("文件名：等待解析")
            return

        selected_indices = self._selected_episode_indices()
        quality = self._selected_quality()
        if not selected_indices or quality is None:
            self.filename_preview_var.set("文件名：选择分集和画质后预览")
            return

        first_name = self._filename_for_episode(selected_indices[0], quality, selected_indices)
        suffix = f" 等 {len(selected_indices)} 个文件" if len(selected_indices) > 1 else ""
        target_dir = self._target_save_dir(self.get_config())
        self.filename_preview_var.set(f"保存到：{target_dir}\n文件名：{first_name}.mp4{suffix}")

    def _on_episode_checkbox_changed(self, index: int) -> None:
        selected = self._selected_episode_indices()
        if not selected:
            self.active_episode_index = None
            self._update_episode_selection_summary()
            self._set_quality_waiting_state("请先选择分集")
            return

        if index in selected:
            self.active_episode_index = index
        elif self.active_episode_index not in selected:
            self.active_episode_index = selected[0]

        self._update_episode_selection_summary()
        if self.active_episode_index is not None:
            self._load_qualities_for_episode(self.active_episode_index)

    def _select_all_episodes(self) -> None:
        if not self.current_media:
            return
        for variable in self.episode_selection_vars.values():
            variable.set(True)
        self.active_episode_index = 0 if self.episode_selection_vars else None
        self._update_episode_selection_summary()
        if self.active_episode_index is not None:
            self._load_qualities_for_episode(self.active_episode_index)

    def _clear_episode_selection(self) -> None:
        if not self.current_media:
            return
        for variable in self.episode_selection_vars.values():
            variable.set(False)
        self.active_episode_index = None
        self._update_episode_selection_summary()
        self._set_quality_waiting_state("请先选择分集")

    def _invert_episode_selection(self) -> None:
        if not self.current_media:
            return
        for variable in self.episode_selection_vars.values():
            variable.set(not variable.get())
        selected = self._selected_episode_indices()
        self.active_episode_index = selected[0] if selected else None
        self._update_episode_selection_summary()
        if self.active_episode_index is not None:
            self._load_qualities_for_episode(self.active_episode_index)
        else:
            self._set_quality_waiting_state("请先选择分集")

    def _parse_current_url(self) -> None:
        if self._is_parsing:
            return

        content = self.url_input.get("1.0", "end").strip()
        if not content:
            self._set_parse_state("warning", "等待链接", "请先粘贴一个 B 站链接", "支持 BV、ep、ss 链接和分享文本")
            self.url_input.focus_set()
            return

        extracted_url = self.api.extract_url(content)
        if extracted_url:
            self._last_auto_parse_url = extracted_url
        requested_url = extracted_url or content
        token = time.time_ns()
        self._parse_request_token = token
        self._quality_request_token = token

        self._is_parsing = True
        self._set_parse_busy(True)
        self._set_parse_state("busy", "正在解析", "正在读取视频信息...", "正在连接 B 站接口并读取分集")
        self._reset_episode_selector("正在解析分集...")
        self._set_quality_waiting_state("正在读取画质")

        self.ui_tasks.run(
            "parse-media",
            lambda: self._parse_media(content),
            on_success=lambda media, token=token, requested_url=requested_url: self._on_parse_success(
                token,
                requested_url,
                media,
            ),
            on_error=lambda exc, token=token, requested_url=requested_url: self._on_parse_failure(
                token,
                requested_url,
                str(exc),
            ),
            on_finally=lambda token=token, requested_url=requested_url: self._finish_parse_request(
                token,
                requested_url,
            ),
        )

    def _parse_media(self, content: str) -> MediaParseResult:
        sessdata = self.refresh_auth_session().strip()
        media = self.api.parse_media(content, sessdata=sessdata)
        self.emit_log(f"解析完成：{media.title}，共 {len(media.episodes)} 个条目")
        return media

    def _finish_parse_request(self, token: int, requested_url: str) -> None:
        if token != self._parse_request_token:
            return
        self._is_parsing = False
        self._set_parse_busy(False)
        current_content = self.url_input.get("1.0", "end").strip()
        current_url = self.api.extract_url(current_content) or current_content
        if current_url and current_url != requested_url:
            self.after(0, self._auto_parse_if_possible)
        elif not current_url:
            self._clear_input()

    def _on_parse_success(self, token: int, requested_url: str, media: MediaParseResult) -> None:
        if not self._parse_result_is_current(token, requested_url):
            return
        self.current_media = media
        source_text = media.source_name or media.folder_title or "未知来源"
        type_text = (
            "番剧 / 合集"
            if media.media_type == "bangumi" or any(item.is_bangumi for item in media.episodes)
            else "普通视频"
        )
        self._set_parse_state(
            "success",
            "解析完成",
            media.title,
            (
                f"类型：{type_text}  |  来源：{source_text}  |  分集：{len(media.episodes)}\n"
                f"保存分组：{media.folder_title or media.title}"
            ),
        )
        self._populate_episode_selector(media)
        self._load_qualities_for_episode(media.default_episode_index)

    def _on_parse_failure(self, token: int, requested_url: str, message: str) -> None:
        if not self._parse_result_is_current(token, requested_url):
            return
        friendly_message = humanize_error(message)
        self.current_media = None
        self.quality_cache.clear()
        self._set_parse_state("error", "解析失败", friendly_message, "可在运行日志中查看详细原因，处理后重试")
        self._reset_episode_selector("解析失败，暂无分集列表")
        self._set_quality_waiting_state("等待画质")

    def _parse_result_is_current(self, token: int, requested_url: str) -> bool:
        if token != self._parse_request_token:
            return False
        current_content = self.url_input.get("1.0", "end").strip()
        current_url = self.api.extract_url(current_content) or current_content
        return current_url == requested_url

    def _load_qualities_for_episode(self, episode_index: int) -> None:
        if not self.current_media or episode_index < 0:
            return

        self.active_episode_index = episode_index
        self._quality_request_token = time.time_ns()
        self._update_episode_selection_summary()

        cached = self.quality_cache.get(episode_index)
        if cached:
            self.current_qualities = cached
            labels = [quality.label for quality in cached]
            self._apply_quality_options(labels)
            self._refresh_add_button_state()
            return

        episode = self.current_media.episodes[episode_index]
        self._set_quality_waiting_state("正在读取画质")
        token = self._quality_request_token
        self.ui_tasks.run(
            "load-qualities",
            lambda: self._load_quality_options(episode),
            on_success=lambda qualities, token=token: self._on_quality_success(token, qualities),
            on_error=lambda exc, token=token: self._on_quality_failure(token, str(exc)),
        )

    def _load_quality_options(self, episode) -> list[QualityOption]:
        sessdata = self.refresh_auth_session().strip()
        return self.api.get_quality_options(episode, sessdata=sessdata)

    def _on_quality_success(self, token: int, qualities: list[QualityOption]) -> None:
        if token != self._quality_request_token:
            return
        if self.active_episode_index is not None:
            self.quality_cache[self.active_episode_index] = qualities
        self.current_qualities = qualities
        labels = [quality.label for quality in qualities]
        self._apply_quality_options(labels)
        self._refresh_add_button_state()

    def _on_quality_failure(self, token: int, message: str) -> None:
        if token != self._quality_request_token:
            return
        self._set_quality_waiting_state("读取失败")
        self.quality_hint_var.set(f"画质读取失败：{humanize_error(message)}")
        notify(self, "画质读取失败，可重新选择分集重试", "error")

    def _add_task_from_selection(self) -> None:
        if self._is_adding:
            return
        if not self.manager.ffmpeg.is_available():
            notify(self, "未找到 FFmpeg，请先在设置中配置 PATH 或选择 ffmpeg.exe", "warning")
            self.open_settings_page()
            return
        if not self.current_media:
            messagebox.showwarning("缺少内容", "请先解析链接。")
            return

        selected_indices = self._selected_episode_indices()
        if not selected_indices:
            messagebox.showwarning("缺少分集", "请先选择至少一个分集或视频。")
            return
        if not self.current_qualities:
            messagebox.showwarning("缺少画质", "当前没有可用的画质列表。")
            return

        config = self.get_config()
        save_dir = self._target_save_dir(config)
        quality = self._selected_quality() or self.current_qualities[0]
        media = self.current_media
        requests = [
            (
                media.episodes[episode_index],
                self._filename_for_episode(episode_index, quality, selected_indices),
            )
            for episode_index in selected_indices
        ]

        self._is_adding = True
        self._refresh_add_button_state()

        def create_selected_tasks() -> int:
            sessdata = self.refresh_auth_session().strip()
            specs = [
                DownloadTaskSpec(
                    source_url=media.normalized_url,
                    episode=episode,
                    quality=quality,
                    save_dir=str(save_dir),
                    sessdata=sessdata or config.sessdata,
                    filename_stem=filename_stem,
                )
                for episode, filename_stem in requests
            ]
            return len(self.manager.create_tasks(specs))

        self.ui_tasks.run(
            "create-download-tasks",
            create_selected_tasks,
            on_success=lambda count: self._on_tasks_created(count, quality),
            on_error=lambda exc: messagebox.showerror("加入队列失败", humanize_error(str(exc))),
            on_finally=self._finish_add_tasks,
        )

    def _on_tasks_created(self, count: int, quality: QualityOption) -> None:
        self.emit_log(f"已创建 {count} 个任务，目标画质 {quality.label}")
        self.show_queue_page()
        notify(self, f"已加入 {count} 个任务 · {quality.label}")

    def _finish_add_tasks(self) -> None:
        self._is_adding = False
        self._refresh_add_button_state()
