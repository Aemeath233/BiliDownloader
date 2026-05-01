from __future__ import annotations

import threading
import time
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from bili_client.core.errors import humanize_error
from bili_client.core.naming import render_filename_template
from bili_client.core.models import AppConfig, MediaParseResult, QualityOption
from bili_client.services.bili_api import BiliApiError, BilibiliApi
from bili_client.services.download_manager import DownloadManager
from bili_client.ui.styles import CARD_RADIUS, FONT_FAMILY, PALETTE, PANEL_RADIUS


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
        super().__init__(master, fg_color="transparent")
        self.api = api
        self.manager = manager
        self.get_config = get_config
        self.emit_log = emit_log
        self.show_queue_page = show_queue_page
        self.open_settings_page = open_settings_page
        self.refresh_auth_session = refresh_auth_session or (lambda: self.get_config().sessdata.strip())

        self.current_media: MediaParseResult | None = None
        self.current_qualities: list[QualityOption] = []
        self.episode_selection_vars: dict[int, ctk.BooleanVar] = {}
        self.episode_checkboxes: dict[int, ctk.CTkCheckBox] = {}
        self.quality_cache: dict[int, list[QualityOption]] = {}
        self.active_episode_index: int | None = None
        self._quality_request_token = 0
        self._is_parsing = False
        self._last_auto_parse_url = ""
        self.preferred_quality_label: str | None = None

        self.parse_status_var = ctk.StringVar(value="等待链接")
        self.media_title_var = ctk.StringVar(value="尚未解析")
        self.media_info_var = ctk.StringVar(value="解析完成后会显示视频信息")
        self.selection_summary_var = ctk.StringVar(value="分集：等待解析")
        self.quality_hint_var = ctk.StringVar(value="画质：等待分集选择")
        self.selection_detail_var = ctk.StringVar(value="未选择分集")
        self.filename_preview_var = ctk.StringVar(value="文件名：等待解析")

        self.grid_columnconfigure(0, weight=5)
        self.grid_columnconfigure(1, weight=4)
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

        ctk.CTkLabel(
            panel,
            text="链接解析",
            anchor="w",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 4))
        ctk.CTkLabel(
            panel,
            text="支持 BV、ep、ss 链接，也可以直接粘贴分享文本",
            anchor="w",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=1, column=0, sticky="ew", padx=18)

        self.url_input = ctk.CTkTextbox(
            panel,
            height=132,
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

        self.parse_button = ctk.CTkButton(
            action_row,
            text="解析链接",
            command=self._parse_current_url,
            fg_color=PALETTE["primary"],
            hover_color=PALETTE["primary_hover"],
            height=40,
            corner_radius=CARD_RADIUS,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
        )
        self.parse_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkButton(
            action_row,
            text="清空",
            command=self._clear_input,
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
            width=86,
            height=40,
            corner_radius=CARD_RADIUS,
        ).grid(row=0, column=1, sticky="e")

        status_panel = ctk.CTkFrame(panel, fg_color=PALETTE["surface"], corner_radius=CARD_RADIUS)
        status_panel.grid(row=4, column=0, sticky="new", padx=18, pady=(18, 12))
        status_panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            status_panel,
            textvariable=self.parse_status_var,
            anchor="w",
            text_color=PALETTE["accent"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 3))
        ctk.CTkLabel(
            status_panel,
            textvariable=self.media_title_var,
            anchor="w",
            justify="left",
            wraplength=620,
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
        ).grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))
        ctk.CTkLabel(
            status_panel,
            textvariable=self.media_info_var,
            anchor="w",
            justify="left",
            wraplength=620,
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))

        config_panel = ctk.CTkFrame(panel, fg_color=PALETTE["surface_alt"], corner_radius=CARD_RADIUS)
        config_panel.grid(row=5, column=0, sticky="ew", padx=18, pady=(0, 18))
        config_panel.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(config_panel, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="当前下载配置",
            anchor="w",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            header,
            text="调整",
            command=self.open_settings_page,
            width=70,
            height=30,
            corner_radius=CARD_RADIUS,
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
        ).grid(row=0, column=1, sticky="e")

        self.config_summary_label = ctk.CTkLabel(
            config_panel,
            text="--",
            anchor="w",
            justify="left",
            wraplength=620,
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.config_summary_label.grid(row=1, column=0, sticky="ew", padx=14, pady=(2, 14))

    def _build_selection_panel(self) -> None:
        panel = ctk.CTkFrame(self, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        panel.grid(row=0, column=1, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(
            panel,
            text="分集与画质",
            anchor="w",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 4))
        ctk.CTkLabel(
            panel,
            textvariable=self.selection_summary_var,
            anchor="w",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=1, column=0, sticky="ew", padx=18)
        ctk.CTkLabel(
            panel,
            textvariable=self.selection_detail_var,
            anchor="w",
            justify="left",
            wraplength=560,
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=2, column=0, sticky="ew", padx=18, pady=(3, 0))

        tools = ctk.CTkFrame(panel, fg_color="transparent")
        tools.grid(row=3, column=0, sticky="ew", padx=18, pady=(14, 10))
        tools.grid_columnconfigure((0, 1, 2), weight=1)
        actions = (
            ("全选", self._select_all_episodes),
            ("反选", self._invert_episode_selection),
            ("清空选择", self._clear_episode_selection),
        )
        for column, (label, handler) in enumerate(actions):
            ctk.CTkButton(
                tools,
                text=label,
                command=handler,
                height=32,
                corner_radius=CARD_RADIUS,
                fg_color=PALETTE["secondary"],
                hover_color=PALETTE["secondary_hover"],
                text_color=PALETTE["text"],
            ).grid(row=0, column=column, sticky="ew", padx=(0, 6) if column < 2 else 0)

        self.episode_scroll = ctk.CTkScrollableFrame(
            panel,
            fg_color=PALETTE["surface"],
            corner_radius=CARD_RADIUS,
        )
        self.episode_scroll.grid(row=4, column=0, sticky="nsew", padx=18, pady=(0, 12))
        self.episode_scroll.grid_columnconfigure(0, weight=1)

        self.episode_placeholder = ctk.CTkLabel(
            self.episode_scroll,
            text="解析完成后会显示可下载分集",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
        )
        self.episode_placeholder.grid(row=0, column=0, pady=28)

        quality_panel = ctk.CTkFrame(panel, fg_color=PALETTE["surface_alt"], corner_radius=CARD_RADIUS)
        quality_panel.grid(row=5, column=0, sticky="ew", padx=18, pady=(0, 18))
        quality_panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
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

        ctk.CTkLabel(
            quality_panel,
            textvariable=self.filename_preview_var,
            anchor="w",
            justify="left",
            wraplength=540,
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))

        self.add_button = ctk.CTkButton(
            quality_panel,
            text="先解析链接",
            command=self._add_task_from_selection,
            fg_color=PALETTE["primary"],
            hover_color=PALETTE["primary_hover"],
            height=42,
            state="disabled",
            corner_radius=CARD_RADIUS,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
        )
        self.add_button.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 14))

    def _bind_shortcuts(self) -> None:
        self.url_input.bind("<Control-Return>", self._submit_parse_shortcut)
        self.url_input.bind("<<Paste>>", self._schedule_auto_parse)
        self.url_input.bind("<Control-v>", self._schedule_auto_parse)

    def _submit_parse_shortcut(self, _event=None):
        self._parse_current_url()
        return "break"

    def _schedule_auto_parse(self, _event=None):
        self.after(120, self._auto_parse_if_possible)

    def _auto_parse_if_possible(self) -> None:
        if self._is_parsing:
            return

        content = self.url_input.get("1.0", "end").strip()
        url = self.api.extract_url(content)
        if not url or url == self._last_auto_parse_url:
            return

        self._last_auto_parse_url = url
        self._parse_current_url()

    def _clear_input(self) -> None:
        self.url_input.delete("1.0", "end")
        self.current_media = None
        self.current_qualities = []
        self.quality_cache.clear()
        self.preferred_quality_label = None
        self.parse_status_var.set("等待链接")
        self.media_title_var.set("尚未解析")
        self.media_info_var.set("解析完成后会显示视频信息")
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

    def update_config_preview(self, config: AppConfig) -> None:
        sessdata_text = "已设置" if config.sessdata.strip() else "未设置"
        speed_limit = f"{config.speed_limit_mb:.2f} MB/s" if config.speed_limit_mb > 0 else "不限速"
        self.config_summary_label.configure(
            text=(
                f"保存目录：{config.save_path}\n"
                f"SESSDATA：{sessdata_text}  |  并发：{config.max_concurrent_tasks}  |  限速：{speed_limit}\n"
                f"命名：{config.filename_template or '{title}'}"
                f"{'  |  按来源分文件夹' if config.organize_by_source else ''}\n"
                f"FFmpeg：{config.ffmpeg_path}"
            )
        )
        self._update_filename_preview()

    def _reset_episode_selector(self, placeholder: str) -> None:
        for checkbox in self.episode_checkboxes.values():
            checkbox.destroy()
        self.episode_checkboxes.clear()
        self.episode_selection_vars.clear()
        self.active_episode_index = None

        self.episode_placeholder.configure(text=placeholder)
        self.episode_placeholder.grid(row=0, column=0, pady=28)
        self.selection_summary_var.set("分集：等待解析")
        self.selection_detail_var.set("未选择分集")
        self.quality_hint_var.set("画质：等待分集选择")
        self.filename_preview_var.set("文件名：等待解析")
        self._refresh_add_button_state()

    def _populate_episode_selector(self, media: MediaParseResult) -> None:
        self._reset_episode_selector("请选择要加入队列的分集")
        self.quality_cache.clear()

        self.episode_placeholder.grid_forget()
        for index, episode in enumerate(media.episodes):
            variable = ctk.BooleanVar(value=index == media.default_episode_index)
            checkbox = ctk.CTkCheckBox(
                self.episode_scroll,
                text=episode.title,
                variable=variable,
                onvalue=True,
                offvalue=False,
                command=lambda idx=index: self._on_episode_checkbox_changed(idx),
                fg_color=PALETTE["primary"],
                hover_color=PALETTE["primary_hover"],
                text_color=PALETTE["text"],
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            )
            checkbox.grid(row=index, column=0, sticky="ew", padx=8, pady=6)
            self.episode_selection_vars[index] = variable
            self.episode_checkboxes[index] = checkbox

        self.active_episode_index = media.default_episode_index
        self._update_episode_selection_summary()

    def _selected_episode_indices(self) -> list[int]:
        return [index for index, variable in self.episode_selection_vars.items() if variable.get()]

    def _selected_episode_preview(self, selected: list[int]) -> str:
        if not self.current_media or not selected:
            return "未选择分集"
        titles = [self.current_media.episodes[index].title for index in selected[:3]]
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
            active_title = self.current_media.episodes[self.active_episode_index].title
        self.selection_summary_var.set(f"分集：已选择 {len(selected)} / {total}")
        self.quality_hint_var.set(f"画质基准：{active_title}")
        self.selection_detail_var.set(self._selected_episode_preview(selected))
        self._update_filename_preview()
        self._refresh_add_button_state()

    def _set_quality_waiting_state(self, text: str) -> None:
        self.quality_combo.configure(values=[text], state="disabled")
        self.quality_combo.set(text)
        self.current_qualities = []
        self._refresh_add_button_state()

    def _refresh_add_button_state(self) -> None:
        selected_count = len(self._selected_episode_indices())
        has_quality = bool(self.current_qualities)

        if not self.current_media:
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
            messagebox.showwarning("缺少链接", "请先粘贴一个 B 站链接。")
            return

        extracted_url = self.api.extract_url(content)
        if extracted_url:
            self._last_auto_parse_url = extracted_url

        self._is_parsing = True
        self.parse_button.configure(state="disabled", text="解析中...")
        self.add_button.configure(state="disabled")
        self.parse_status_var.set("正在解析")
        self.media_title_var.set("正在读取视频信息...")
        self._reset_episode_selector("正在解析分集...")
        self._set_quality_waiting_state("正在读取画质")

        threading.Thread(target=self._parse_worker, args=(content,), daemon=True).start()

    def _parse_worker(self, content: str) -> None:
        try:
            sessdata = self.refresh_auth_session().strip()
            media = self.api.parse_media(content, sessdata=sessdata)
            self.after(0, lambda: self._on_parse_success(media))
            self.emit_log(f"解析完成：{media.title}，共 {len(media.episodes)} 个条目")
        except BiliApiError as exc:
            self.after(0, lambda: self._on_parse_failure(str(exc)))
        except Exception as exc:
            self.after(0, lambda: self._on_parse_failure(str(exc)))
        finally:
            self.after(0, self._finish_parse_request)

    def _finish_parse_request(self) -> None:
        self._is_parsing = False
        self.parse_button.configure(state="normal", text="解析链接")

    def _on_parse_success(self, media: MediaParseResult) -> None:
        self.current_media = media
        self.parse_status_var.set("解析完成")
        self.media_title_var.set(media.title)
        source_text = media.source_name or media.folder_title or "未知来源"
        type_text = "番剧 / 合集" if media.media_type == "bangumi" or any(item.is_bangumi for item in media.episodes) else "普通视频"
        self.media_info_var.set(
            f"类型：{type_text}  |  来源：{source_text}  |  分集：{len(media.episodes)}\n"
            f"保存分组：{media.folder_title or media.title}"
        )
        self._populate_episode_selector(media)
        self._load_qualities_for_episode(media.default_episode_index)

    def _on_parse_failure(self, message: str) -> None:
        friendly_message = humanize_error(message)
        self.current_media = None
        self.quality_cache.clear()
        self.parse_status_var.set("解析失败")
        self.media_title_var.set(friendly_message)
        self.media_info_var.set("请检查链接或登录状态后重试")
        self._reset_episode_selector("解析失败，暂无分集列表")
        self._set_quality_waiting_state("等待画质")
        messagebox.showerror("解析失败", friendly_message)

    def _load_qualities_for_episode(self, episode_index: int) -> None:
        if not self.current_media or episode_index < 0:
            return

        self.active_episode_index = episode_index
        self._update_episode_selection_summary()

        cached = self.quality_cache.get(episode_index)
        if cached:
            self.current_qualities = cached
            labels = [quality.label for quality in cached]
            self._apply_quality_options(labels)
            self._refresh_add_button_state()
            return

        token = time.time_ns()
        self._quality_request_token = token
        episode = self.current_media.episodes[episode_index]
        self._set_quality_waiting_state("正在读取画质")
        threading.Thread(target=self._quality_worker, args=(token, episode), daemon=True).start()

    def _quality_worker(self, token: int, episode) -> None:
        try:
            sessdata = self.refresh_auth_session().strip()
            qualities = self.api.get_quality_options(episode, sessdata=sessdata)
            self.after(0, lambda: self._on_quality_success(token, qualities))
        except Exception as exc:
            self.after(0, lambda: self._on_quality_failure(token, str(exc)))

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
        messagebox.showerror("读取画质失败", humanize_error(message))

    def _add_task_from_selection(self) -> None:
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

        sessdata = self.refresh_auth_session().strip()
        config = self.get_config()
        save_dir = self._target_save_dir(config)
        save_dir.mkdir(parents=True, exist_ok=True)

        quality = self._selected_quality() or self.current_qualities[0]

        for episode_index in selected_indices:
            filename_stem = self._filename_for_episode(episode_index, quality, selected_indices)
            self.manager.create_task(
                source_url=self.current_media.normalized_url,
                episode=self.current_media.episodes[episode_index],
                quality=quality,
                save_dir=str(save_dir),
                sessdata=sessdata or config.sessdata,
                filename_stem=filename_stem,
            )

        self.emit_log(f"已创建 {len(selected_indices)} 个任务，画质 {quality.label}")
        self.show_queue_page()
