from __future__ import annotations

import time
from pathlib import Path

import customtkinter as ctk

from bili_client.core.formatters import format_bytes, format_percent, format_speed, format_status
from bili_client.ui.styles import (
    BUTTON_HEIGHT,
    CARD_RADIUS,
    FONT_FAMILY,
    PALETTE,
    PANEL_RADIUS,
    STATUS_STYLES,
    button_style,
)
from bili_client.ui.widgets import MotionButton, MotionProgressBar, TextEntry, TextLabel

FILTER_OPTIONS = {
    "全部": "all",
    "进行中": "active",
    "排队中": "queued",
    "已完成": "completed",
    "失败": "failed",
    "已取消": "canceled",
    "已结束": "ended",
}


def format_queue_position(snapshot: dict) -> str:
    if snapshot.get("queue_position"):
        return f"队列 #{snapshot['queue_position']}"
    if snapshot["status"] in {"preparing", "downloading", "merging"}:
        return "处理中"
    if snapshot["status"] == "completed":
        return "已完成"
    if snapshot["status"] == "failed":
        return "已失败"
    if snapshot["status"] == "canceled":
        return "已取消"
    return "--"


def matches_filter(snapshot: dict, filter_key: str) -> bool:
    status = snapshot["status"]
    if filter_key == "all":
        return True
    if filter_key == "active":
        return status in {"preparing", "downloading", "merging"}
    if filter_key == "queued":
        return status == "queued"
    if filter_key == "ended":
        return status in {"completed", "failed", "canceled"}
    return status == filter_key


class TaskCard(ctk.CTkFrame):
    def __init__(self, master, task_id: str, on_cancel, on_retry, on_remove, on_open_dir) -> None:
        super().__init__(
            master,
            fg_color=PALETTE["panel_bg"],
            corner_radius=CARD_RADIUS,
            border_width=1,
            border_color=PALETTE["border"],
        )
        self.task_id = task_id
        self.on_cancel = on_cancel
        self.on_retry = on_retry
        self.on_remove = on_remove
        self.on_open_dir = on_open_dir
        self.save_dir = ""
        self._last_snapshot: dict | None = None
        self._render_values: dict = {}
        self._indeterminate = False
        self.grid_columnconfigure(0, weight=1)

        self.title_label = TextLabel(
            self,
            text="",
            anchor="w",
            justify="left",
            wraplength=660,
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
        )
        self.title_label.grid(row=0, column=0, sticky="ew", padx=(14, 10), pady=(12, 4))

        self.status_label = TextLabel(
            self,
            text="",
            width=74,
            height=26,
            corner_radius=CARD_RADIUS,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        )
        self.status_label.grid(row=0, column=1, sticky="e", padx=(0, 8), pady=(12, 4))

        self.open_button = MotionButton(
            self,
            text="目录",
            width=64,
            height=28,
            command=lambda: self.on_open_dir(self.save_dir),
            corner_radius=CARD_RADIUS,
            **button_style("secondary"),
        )
        self.open_button.grid(row=0, column=2, sticky="e", padx=(0, 8), pady=(12, 4))

        self.retry_button = MotionButton(
            self,
            text="重试",
            width=64,
            height=28,
            command=lambda: self.on_retry(self.task_id),
            corner_radius=CARD_RADIUS,
            **button_style("secondary"),
        )
        self.retry_button.grid(row=0, column=3, sticky="e", padx=(0, 8), pady=(12, 4))

        self.remove_button = MotionButton(
            self,
            text="移除",
            width=64,
            height=28,
            command=lambda: self.on_remove(self.task_id),
            corner_radius=CARD_RADIUS,
            **button_style("secondary"),
        )
        self.remove_button.grid(row=0, column=4, sticky="e", padx=(0, 8), pady=(12, 4))

        self.cancel_button = MotionButton(
            self,
            text="取消",
            width=68,
            height=28,
            command=lambda: self.on_cancel(self.task_id),
            corner_radius=CARD_RADIUS,
            **button_style("danger"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        )
        self.cancel_button.grid(row=0, column=5, sticky="e", padx=(0, 14), pady=(12, 4))

        self.meta_label = TextLabel(
            self,
            text="",
            anchor="w",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.meta_label.grid(row=1, column=0, columnspan=6, sticky="ew", padx=14)

        self.progress = MotionProgressBar(self, height=7, corner_radius=8, progress_color=PALETTE["primary"])
        self.progress.grid(row=2, column=0, columnspan=6, sticky="ew", padx=14, pady=(12, 7))
        self.progress.set(0)

        info_row = ctk.CTkFrame(self, fg_color="transparent")
        info_row.grid(row=3, column=0, columnspan=6, sticky="ew", padx=14, pady=(0, 4))
        info_row.grid_columnconfigure((0, 1), weight=1)

        self.progress_label = TextLabel(
            info_row,
            text="",
            anchor="w",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.progress_label.grid(row=0, column=0, sticky="ew")

        self.stats_label = TextLabel(
            info_row,
            text="",
            anchor="e",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.stats_label.grid(row=0, column=1, sticky="ew")

        self.detail_label = TextLabel(
            self,
            text="",
            anchor="w",
            justify="left",
            wraplength=940,
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.detail_label.grid(row=4, column=0, columnspan=6, sticky="ew", padx=14, pady=(0, 12))

    def update_card(self, snapshot: dict) -> None:
        if snapshot == self._last_snapshot:
            return
        self._last_snapshot = dict(snapshot)
        bg_color, text_color = STATUS_STYLES.get(snapshot["status"], STATUS_STYLES["queued"])
        self._configure_changed(
            self.status_label, text=format_status(snapshot["status"]), fg_color=bg_color, text_color=text_color
        )
        self._configure_changed(self.title_label, text=snapshot["title"])

        self.save_dir = snapshot["save_dir"]
        output_name = Path(snapshot["output_path"]).name
        self._configure_changed(self.meta_label, text=f"{snapshot['quality_label']}  ·  {output_name}")

        progress_value = (snapshot["percent"] or 0) / 100 if snapshot["total_bytes"] else 0
        if snapshot["status"] == "completed":
            progress_value = 1.0
        indeterminate = snapshot["status"] in {"preparing", "merging"} or (
            snapshot["status"] == "downloading" and not snapshot["total_bytes"]
        )
        if indeterminate != self._indeterminate:
            self._indeterminate = indeterminate
            self.progress.settle()
            self.progress.configure(mode="indeterminate" if indeterminate else "determinate")
            if indeterminate:
                self.progress.start()
            else:
                self.progress.stop()
        self.progress.set(progress_value)
        self._configure_changed(
            self.progress_label, text=f"{format_percent(snapshot['percent'])}  ·  {snapshot['message']}"
        )

        downloaded = format_bytes(snapshot["downloaded_bytes"]) if snapshot["downloaded_bytes"] else "0 B"
        total = format_bytes(snapshot["total_bytes"])
        speed = format_speed(snapshot["speed_bps"])
        self._configure_changed(
            self.stats_label, text=f"{downloaded} / {total}  ·  {speed}  ·  {format_queue_position(snapshot)}"
        )

        self._configure_changed(self.open_button, state="normal" if self.save_dir else "disabled")
        if snapshot.get("can_retry"):
            self._configure_changed(self.retry_button, state="normal", **button_style("primary"))
        else:
            self._configure_changed(self.retry_button, state="disabled", **button_style("secondary"))
        self._configure_changed(self.remove_button, state="normal" if snapshot.get("can_remove") else "disabled")
        self._configure_changed(
            self.cancel_button,
            state="normal" if snapshot["can_cancel"] else "disabled",
            text="取消中" if snapshot["cancel_requested"] else "取消",
        )

        if snapshot["status"] == "completed":
            detail_text = f"保存位置：{snapshot['output_path']}"
        elif snapshot["error"]:
            detail_text = snapshot["error"]
        else:
            detail_text = f"输出位置：{snapshot['output_path']}"
        self._configure_changed(self.detail_label, text=detail_text)

    def _configure_changed(self, widget, **values) -> None:
        previous = self._render_values.setdefault(widget, {})
        changed = {key: value for key, value in values.items() if previous.get(key) != value}
        if changed:
            widget.configure(**changed)
            previous.update(changed)

    def set_active(self, active: bool) -> None:
        if active and self._indeterminate:
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.settle()


class QueuePage(ctk.CTkFrame):
    PAGE_SIZE = 30

    def __init__(
        self,
        master,
        on_cancel_task,
        on_retry_task,
        on_remove_task,
        on_retry_failed,
        on_clear_finished,
        on_open_dir,
    ) -> None:
        super().__init__(master, fg_color=PALETTE["app_bg"])
        self.on_cancel_task = on_cancel_task
        self.on_retry_task = on_retry_task
        self.on_remove_task = on_remove_task
        self.on_retry_failed = on_retry_failed
        self.on_clear_finished = on_clear_finished
        self.on_open_dir = on_open_dir
        self.task_cards: dict[str, TaskCard] = {}
        self._last_tasks: list[dict] = []
        self._visible_task_ids: tuple[str, ...] = ()
        self._empty_state_visible = True
        self._task_count_text = "0 个任务"
        self._current_page = 0
        self._search_after_id = None
        self._pagination_state = None
        self._active = False
        self._card_after_id = None
        self._pending_card_ids: list[str] = []
        self._render_snapshots: dict[str, dict] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        overview = ctk.CTkFrame(self, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        overview.grid(row=0, column=0, sticky="ew")
        overview.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.queue_total_tasks_var = ctk.StringVar(value="0")
        self.queue_running_tasks_var = ctk.StringVar(value="0")
        self.queue_waiting_tasks_var = ctk.StringVar(value="0")
        self.queue_ended_tasks_var = ctk.StringVar(value="0")

        metrics = (
            ("总任务", self.queue_total_tasks_var),
            ("进行中", self.queue_running_tasks_var),
            ("排队中", self.queue_waiting_tasks_var),
            ("已结束", self.queue_ended_tasks_var),
        )
        for column, (label, variable) in enumerate(metrics):
            self._build_metric_card(overview, column, label, variable)

        list_panel = ctk.CTkFrame(self, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        list_panel.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        list_panel.grid_rowconfigure(1, weight=1)
        list_panel.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(list_panel, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 10))
        header.grid_columnconfigure(0, weight=1)

        copy = ctk.CTkFrame(header, fg_color="transparent")
        copy.grid(row=0, column=0, sticky="w")
        TextLabel(
            copy,
            text="任务列表",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
        ).pack(anchor="w")
        self.task_count_label = TextLabel(
            copy,
            text="0 个任务",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.task_count_label.pack(anchor="w", pady=(3, 0))

        self.search_entry = TextEntry(
            header,
            placeholder_text="搜索标题或文件名",
            width=250,
            height=34,
            fg_color=PALETTE["input_bg"],
            border_color=PALETTE["border"],
            text_color=PALETTE["text"],
        )
        self.search_entry.grid(row=0, column=1, sticky="e")
        self.search_entry.bind("<KeyRelease>", self._schedule_search)
        self.search_entry.bind("<<Paste>>", self._schedule_search)

        action_row = ctk.CTkFrame(header, fg_color="transparent")
        action_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        action_row.grid_columnconfigure(0, weight=1)

        self.filter_segment = ctk.CTkSegmentedButton(
            action_row,
            values=list(FILTER_OPTIONS.keys()),
            command=self._on_filter_changed,
            height=34,
            corner_radius=CARD_RADIUS,
            fg_color=PALETTE["surface"],
            selected_color=PALETTE["primary"],
            selected_hover_color=PALETTE["primary_hover"],
            unselected_color=PALETTE["surface"],
            unselected_hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
        )
        self.filter_segment.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.filter_segment.set("全部")

        MotionButton(
            action_row,
            text="重试失败/取消",
            command=self.on_retry_failed,
            width=116,
            height=BUTTON_HEIGHT,
            corner_radius=CARD_RADIUS,
            **button_style("secondary"),
        ).grid(row=0, column=1, padx=(0, 8))

        MotionButton(
            action_row,
            text="清理已结束",
            command=self.on_clear_finished,
            width=104,
            height=BUTTON_HEIGHT,
            corner_radius=CARD_RADIUS,
            **button_style("secondary"),
        ).grid(row=0, column=2)

        self.task_scroll = ctk.CTkScrollableFrame(list_panel, fg_color="transparent")
        self.task_scroll.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 12))
        self.task_scroll.grid_columnconfigure(0, weight=1)

        self.empty_label = TextLabel(
            self.task_scroll,
            text="暂无下载任务\n解析链接后加入队列",
            text_color=PALETTE["muted"],
            justify="center",
            font=ctk.CTkFont(family=FONT_FAMILY, size=15),
        )
        self.empty_label.grid(row=0, column=0, pady=48)

        pagination = ctk.CTkFrame(list_panel, fg_color="transparent")
        pagination.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 12))
        pagination.grid_columnconfigure(0, weight=1)
        self.page_label = TextLabel(pagination, text="第 1 / 1 页", text_color=PALETTE["muted"], anchor="w")
        self.page_label.grid(row=0, column=0, sticky="w")
        self.previous_button = MotionButton(
            pagination, text="上一页", width=80, height=30, command=lambda: self._change_page(-1), **button_style()
        )
        self.previous_button.grid(row=0, column=1, padx=(0, 8))
        self.next_button = MotionButton(
            pagination, text="下一页", width=80, height=30, command=lambda: self._change_page(1), **button_style()
        )
        self.next_button.grid(row=0, column=2)

    def _build_metric_card(self, master, column: int, label: str, variable: ctk.StringVar) -> None:
        card = ctk.CTkFrame(master, fg_color=PALETTE["surface"], corner_radius=CARD_RADIUS)
        card.grid(row=0, column=column, sticky="ew", padx=(12 if column == 0 else 4, 12 if column == 3 else 4), pady=12)
        TextLabel(
            card,
            text=label,
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).pack(anchor="w", padx=14, pady=(10, 3))
        TextLabel(
            card,
            textvariable=variable,
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=22, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(0, 12))

    def _on_filter_changed(self, _value: str) -> None:
        self._current_page = 0
        self._render_tasks(self._last_tasks)
        self._scroll_to_top()

    def _schedule_search(self, _event=None) -> None:
        if self._search_after_id is not None:
            self.after_cancel(self._search_after_id)
        self._search_after_id = self.after(180, self._apply_search)

    def _apply_search(self) -> None:
        self._search_after_id = None
        self._on_filter_changed("")

    def _change_page(self, delta: int) -> None:
        self._current_page = max(0, self._current_page + delta)
        self._render_tasks(self._last_tasks)
        self._scroll_to_top()

    def _scroll_to_top(self) -> None:
        self.task_scroll._parent_canvas.yview_moveto(0)

    def set_active(self, active: bool) -> None:
        self._active = active
        if not active and self._card_after_id is not None:
            self.after_cancel(self._card_after_id)
            self._card_after_id = None
        elif active and self._pending_card_ids and self._card_after_id is None:
            self._card_after_id = self.after(0, self._create_pending_cards)
        for card in self.task_cards.values():
            card.set_active(active)

    def show_failed_tasks(self) -> None:
        self.filter_segment.set("失败")
        self._current_page = 0
        self._render_tasks(self._last_tasks)

    def update_snapshot(self, metrics: dict, tasks: list[dict]) -> None:
        total_tasks = (
            metrics["active_count"]
            + metrics["queued_count"]
            + metrics["completed_count"]
            + metrics["failed_count"]
            + metrics["canceled_count"]
        )
        ended_tasks = metrics["completed_count"] + metrics["failed_count"] + metrics["canceled_count"]

        self._set_string_var(self.queue_total_tasks_var, str(total_tasks))
        self._set_string_var(self.queue_running_tasks_var, str(metrics["active_count"]))
        self._set_string_var(self.queue_waiting_tasks_var, str(metrics["queued_count"]))
        self._set_string_var(self.queue_ended_tasks_var, str(ended_tasks))
        self._last_tasks = tasks
        self._render_tasks(tasks)

    @staticmethod
    def _set_string_var(variable: ctk.StringVar, value: str) -> None:
        if variable.get() != value:
            variable.set(value)

    def _render_tasks(self, tasks: list[dict]) -> None:
        filter_key = FILTER_OPTIONS.get(self.filter_segment.get(), "all")
        keyword = self.search_entry.get().strip().casefold()
        filtered_tasks = [
            task
            for task in tasks
            if matches_filter(task, filter_key)
            and (not keyword or keyword in f"{task['title']} {task['output_path']}".casefold())
        ]
        pages = max(1, (len(filtered_tasks) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self._current_page = min(self._current_page, pages - 1)
        start = self._current_page * self.PAGE_SIZE
        visible_tasks = filtered_tasks[start : start + self.PAGE_SIZE]
        pagination_state = (self._current_page, pages)
        if pagination_state != self._pagination_state:
            self._pagination_state = pagination_state
            self.page_label.configure(text=f"第 {self._current_page + 1} / {pages} 页")
            self.previous_button.configure(state="normal" if self._current_page else "disabled")
            self.next_button.configure(state="normal" if self._current_page < pages - 1 else "disabled")
        visible_ids = tuple(task["task_id"] for task in visible_tasks)
        layout_changed = visible_ids != self._visible_task_ids
        task_count_text = f"{len(filtered_tasks)} 个匹配任务 · 共 {len(tasks)} 个任务"
        if task_count_text != self._task_count_text:
            self.task_count_label.configure(text=task_count_text)
            self._task_count_text = task_count_text

        should_show_empty = not visible_tasks
        if should_show_empty != self._empty_state_visible:
            if should_show_empty:
                self.empty_label.grid(row=0, column=0, pady=48)
            else:
                self.empty_label.grid_forget()
            self._empty_state_visible = should_show_empty

        live_ids = set(visible_ids)
        self._render_snapshots = {task["task_id"]: task for task in visible_tasks}
        for stale_id in list(self.task_cards):
            if stale_id not in live_ids:
                self.task_cards[stale_id].destroy()
                del self.task_cards[stale_id]

        for index, task in enumerate(visible_tasks):
            task_id = task["task_id"]
            card = self.task_cards.get(task_id)
            if card is None:
                continue
            if layout_changed:
                card.grid(row=index, column=0, sticky="ew", padx=8, pady=6)
            card.update_card(task)

        self._visible_task_ids = visible_ids
        self._pending_card_ids = [task_id for task_id in visible_ids if task_id not in self.task_cards]
        if self._active and self._pending_card_ids and self._card_after_id is None:
            self._card_after_id = self.after(0, self._create_pending_cards)

    def _create_pending_cards(self) -> None:
        self._card_after_id = None
        deadline = time.monotonic() + 0.010
        for _ in range(2):
            if not self._active or not self._pending_card_ids:
                break
            task_id = self._pending_card_ids.pop(0)
            snapshot = self._render_snapshots.get(task_id)
            if snapshot is None:
                continue
            card = TaskCard(
                self.task_scroll,
                task_id=task_id,
                on_cancel=self.on_cancel_task,
                on_retry=self.on_retry_task,
                on_remove=self.on_remove_task,
                on_open_dir=self.on_open_dir,
            )
            self.task_cards[task_id] = card
            card.grid(row=self._visible_task_ids.index(task_id), column=0, sticky="ew", padx=8, pady=6)
            card.update_card(snapshot)
            if time.monotonic() >= deadline:
                break
        if self._active and self._pending_card_ids:
            self._card_after_id = self.after(8, self._create_pending_cards)
