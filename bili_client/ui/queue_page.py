from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from bili_client.core.formatters import format_bytes, format_percent, format_speed, format_status
from bili_client.ui.styles import CARD_RADIUS, FONT_FAMILY, PALETTE, PANEL_RADIUS, STATUS_STYLES


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
        self.grid_columnconfigure(0, weight=1)

        self.title_label = ctk.CTkLabel(
            self,
            text="",
            anchor="w",
            justify="left",
            wraplength=660,
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
        )
        self.title_label.grid(row=0, column=0, sticky="ew", padx=(14, 10), pady=(12, 4))

        self.status_label = ctk.CTkLabel(
            self,
            text="",
            width=74,
            height=26,
            corner_radius=CARD_RADIUS,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        )
        self.status_label.grid(row=0, column=1, sticky="e", padx=(0, 8), pady=(12, 4))

        self.open_button = ctk.CTkButton(
            self,
            text="目录",
            width=64,
            height=28,
            command=lambda: self.on_open_dir(self.save_dir),
            corner_radius=CARD_RADIUS,
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
        )
        self.open_button.grid(row=0, column=2, sticky="e", padx=(0, 8), pady=(12, 4))

        self.retry_button = ctk.CTkButton(
            self,
            text="重试",
            width=64,
            height=28,
            command=lambda: self.on_retry(self.task_id),
            corner_radius=CARD_RADIUS,
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
        )
        self.retry_button.grid(row=0, column=3, sticky="e", padx=(0, 8), pady=(12, 4))

        self.remove_button = ctk.CTkButton(
            self,
            text="移除",
            width=64,
            height=28,
            command=lambda: self.on_remove(self.task_id),
            corner_radius=CARD_RADIUS,
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
        )
        self.remove_button.grid(row=0, column=4, sticky="e", padx=(0, 8), pady=(12, 4))

        self.cancel_button = ctk.CTkButton(
            self,
            text="取消",
            width=68,
            height=28,
            command=lambda: self.on_cancel(self.task_id),
            corner_radius=CARD_RADIUS,
            fg_color=PALETTE["danger"],
            hover_color=PALETTE["danger_hover"],
            text_color=PALETTE["text_inverse"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        )
        self.cancel_button.grid(row=0, column=5, sticky="e", padx=(0, 14), pady=(12, 4))

        self.meta_label = ctk.CTkLabel(
            self,
            text="",
            anchor="w",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.meta_label.grid(row=1, column=0, columnspan=6, sticky="ew", padx=14)

        self.progress = ctk.CTkProgressBar(self, height=9, corner_radius=8, progress_color=PALETTE["primary"])
        self.progress.grid(row=2, column=0, columnspan=6, sticky="ew", padx=14, pady=(12, 7))
        self.progress.set(0)

        info_row = ctk.CTkFrame(self, fg_color="transparent")
        info_row.grid(row=3, column=0, columnspan=6, sticky="ew", padx=14, pady=(0, 4))
        info_row.grid_columnconfigure((0, 1), weight=1)

        self.progress_label = ctk.CTkLabel(
            info_row,
            text="",
            anchor="w",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.progress_label.grid(row=0, column=0, sticky="ew")

        self.stats_label = ctk.CTkLabel(
            info_row,
            text="",
            anchor="e",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.stats_label.grid(row=0, column=1, sticky="ew")

        self.detail_label = ctk.CTkLabel(
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
        bg_color, text_color = STATUS_STYLES.get(snapshot["status"], STATUS_STYLES["queued"])
        self.status_label.configure(text=format_status(snapshot["status"]), fg_color=bg_color, text_color=text_color)
        self.title_label.configure(text=snapshot["title"])

        self.save_dir = snapshot["save_dir"]
        output_name = Path(snapshot["output_path"]).name
        self.meta_label.configure(text=f"{snapshot['quality_label']}  |  {output_name}")

        progress_value = (snapshot["percent"] or 0) / 100 if snapshot["total_bytes"] else 0
        self.progress.set(progress_value)
        self.progress_label.configure(text=f"{format_percent(snapshot['percent'])}  |  {snapshot['message']}")

        downloaded = format_bytes(snapshot["downloaded_bytes"]) if snapshot["downloaded_bytes"] else "0 B"
        total = format_bytes(snapshot["total_bytes"])
        speed = format_speed(snapshot["speed_bps"])
        self.stats_label.configure(text=f"{downloaded} / {total}  |  {speed}  |  {format_queue_position(snapshot)}")

        self.open_button.configure(state="normal" if self.save_dir else "disabled")
        self.retry_button.configure(state="normal" if snapshot.get("can_retry") else "disabled")
        self.remove_button.configure(state="normal" if snapshot.get("can_remove") else "disabled")
        self.cancel_button.configure(
            state="normal" if snapshot["can_cancel"] else "disabled",
            text="取消中" if snapshot["cancel_requested"] else "取消",
        )

        if snapshot["status"] == "completed":
            detail_text = f"保存位置：{snapshot['output_path']}"
        elif snapshot["error"]:
            detail_text = snapshot["error"]
        else:
            detail_text = f"输出位置：{snapshot['output_path']}"
        self.detail_label.configure(text=detail_text)


class QueuePage(ctk.CTkFrame):
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
        super().__init__(master, fg_color="transparent")
        self.on_cancel_task = on_cancel_task
        self.on_retry_task = on_retry_task
        self.on_remove_task = on_remove_task
        self.on_retry_failed = on_retry_failed
        self.on_clear_finished = on_clear_finished
        self.on_open_dir = on_open_dir
        self.task_cards: dict[str, TaskCard] = {}
        self._last_tasks: list[dict] = []

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
        ctk.CTkLabel(
            copy,
            text="任务列表",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
        ).pack(anchor="w")
        self.task_count_label = ctk.CTkLabel(
            copy,
            text="0 个任务",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.task_count_label.pack(anchor="w", pady=(3, 0))

        action_row = ctk.CTkFrame(header, fg_color="transparent")
        action_row.grid(row=0, column=1, sticky="e")

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
        self.filter_segment.grid(row=0, column=0, padx=(0, 8))
        self.filter_segment.set("全部")

        ctk.CTkButton(
            action_row,
            text="重试失败/取消",
            command=self.on_retry_failed,
            width=116,
            height=34,
            corner_radius=CARD_RADIUS,
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
        ).grid(row=0, column=1, padx=(0, 8))

        ctk.CTkButton(
            action_row,
            text="清理已结束",
            command=self.on_clear_finished,
            width=104,
            height=34,
            corner_radius=CARD_RADIUS,
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
        ).grid(row=0, column=2)

        self.task_scroll = ctk.CTkScrollableFrame(list_panel, fg_color="transparent")
        self.task_scroll.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 12))
        self.task_scroll.grid_columnconfigure(0, weight=1)

        self.empty_label = ctk.CTkLabel(
            self.task_scroll,
            text="暂无下载任务\n解析链接后加入队列",
            text_color=PALETTE["muted"],
            justify="center",
            font=ctk.CTkFont(family=FONT_FAMILY, size=15),
        )
        self.empty_label.grid(row=0, column=0, pady=48)

    def _build_metric_card(self, master, column: int, label: str, variable: ctk.StringVar) -> None:
        card = ctk.CTkFrame(master, fg_color=PALETTE["surface"], corner_radius=CARD_RADIUS)
        card.grid(row=0, column=column, sticky="ew", padx=(12 if column == 0 else 4, 12 if column == 3 else 4), pady=12)
        ctk.CTkLabel(
            card,
            text=label,
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).pack(anchor="w", padx=14, pady=(10, 3))
        ctk.CTkLabel(
            card,
            textvariable=variable,
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=22, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(0, 12))

    def _on_filter_changed(self, _value: str) -> None:
        self._render_tasks(self._last_tasks)

    def show_failed_tasks(self) -> None:
        self.filter_segment.set("失败")
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

        self.queue_total_tasks_var.set(str(total_tasks))
        self.queue_running_tasks_var.set(str(metrics["active_count"]))
        self.queue_waiting_tasks_var.set(str(metrics["queued_count"]))
        self.queue_ended_tasks_var.set(str(ended_tasks))
        self._last_tasks = tasks
        self._render_tasks(tasks)

    def _render_tasks(self, tasks: list[dict]) -> None:
        filter_key = FILTER_OPTIONS.get(self.filter_segment.get(), "all")
        visible_tasks = [task for task in tasks if matches_filter(task, filter_key)]
        self.task_count_label.configure(text=f"显示 {len(visible_tasks)} / {len(tasks)} 个任务")

        if not visible_tasks:
            self.empty_label.grid(row=0, column=0, pady=48)
        else:
            self.empty_label.grid_forget()

        live_ids = set()
        for index, task in enumerate(visible_tasks):
            task_id = task["task_id"]
            live_ids.add(task_id)
            card = self.task_cards.get(task_id)
            if card is None:
                card = TaskCard(
                    self.task_scroll,
                    task_id=task_id,
                    on_cancel=self.on_cancel_task,
                    on_retry=self.on_retry_task,
                    on_remove=self.on_remove_task,
                    on_open_dir=self.on_open_dir,
                )
                self.task_cards[task_id] = card
            card.grid(row=index, column=0, sticky="ew", padx=8, pady=6)
            card.update_card(task)

        for stale_id in list(self.task_cards):
            if stale_id not in live_ids:
                self.task_cards[stale_id].grid_forget()
                if all(task["task_id"] != stale_id for task in tasks):
                    self.task_cards[stale_id].destroy()
                    del self.task_cards[stale_id]
