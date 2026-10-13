from __future__ import annotations

from bili_client.core.models import TaskStatus


def format_bytes(value: int | float | None) -> str:
    if value is None or value <= 0:
        return "--"

    size = float(value)
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1

    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.2f} {units[idx]}"


def format_speed(value: float | None) -> str:
    if value is None or value <= 0:
        return "0 B/s"
    return f"{format_bytes(value)}/s"


def format_percent(value: float | None) -> str:
    if value is None or value <= 0:
        return "0.0%"
    return f"{value:.1f}%"


def format_status(status: str | TaskStatus) -> str:
    status_value = status.value if isinstance(status, TaskStatus) else status
    labels = {
        TaskStatus.QUEUED.value: "排队中",
        TaskStatus.PREPARING.value: "准备中",
        TaskStatus.DOWNLOADING.value: "下载中",
        TaskStatus.MERGING.value: "合并中",
        TaskStatus.COMPLETED.value: "已完成",
        TaskStatus.FAILED.value: "失败",
        TaskStatus.CANCELED.value: "已取消",
    }
    return labels.get(status_value, status_value)


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return "--"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
