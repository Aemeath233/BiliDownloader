from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_filename(name: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("_", str(name or "")).strip().rstrip(". ")
    if not cleaned:
        return "untitled"
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:180].rstrip(". ") or "untitled"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    DOWNLOADING = "downloading"
    MERGING = "merging"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(slots=True)
class AppConfig:
    sessdata: str = ""
    save_path: str = str(Path.cwd() / "video")
    organize_by_source: bool = False
    filename_template: str = "{title}"
    cleanup_temp_files: bool = True
    steady_mode: bool = True
    speed_limit_mb: float = 0.0
    max_concurrent_tasks: int = 2
    ffmpeg_path: str = "ffmpeg"
    appearance_mode: str = "system"
    animations_enabled: bool = True


@dataclass(slots=True)
class EpisodeInfo:
    title: str
    bvid: str
    cid: int
    is_bangumi: bool


@dataclass(slots=True)
class QualityOption:
    qn: int
    label: str


@dataclass(slots=True)
class MediaParseResult:
    source_url: str
    normalized_url: str
    title: str
    episodes: list[EpisodeInfo]
    default_episode_index: int = 0
    folder_title: str = ""
    source_name: str = ""
    media_type: str = "video"


@dataclass(slots=True)
class StreamInfo:
    kind: str
    url: str
    size_bytes: int = 0
    backup_urls: list[str] = field(default_factory=list)
    referer: str = ""
    quality_qn: int = 0
    quality_label: str = ""


@dataclass(slots=True)
class ProgressSnapshot:
    total_bytes: int = 0
    downloaded_bytes: int = 0
    percent: float = 0.0
    speed_bps: float = 0.0
    video_total_bytes: int = 0
    video_downloaded_bytes: int = 0
    audio_total_bytes: int = 0
    audio_downloaded_bytes: int = 0


@dataclass
class DownloadTask:
    task_id: str
    source_url: str
    output_path: str
    save_dir: str
    sessdata: str
    episode: EpisodeInfo
    quality: QualityOption
    title: str
    status: TaskStatus = TaskStatus.QUEUED
    message: str = "等待开始"
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    progress: ProgressSnapshot = field(default_factory=ProgressSnapshot)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _speed_samples: deque[tuple[float, int]] = field(default_factory=deque, repr=False)
