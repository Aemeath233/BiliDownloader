from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path

from bili_client.core.models import AppConfig
from bili_client.services.file_io import atomic_write_text


class ConfigStore:
    def __init__(self, app_name: str = "BiliDownloader") -> None:
        appdata = Path(os.getenv("APPDATA") or Path.home())
        self.config_dir = appdata / app_name
        self.config_file = self.config_dir / "config.json"
        self.auth_file = self.config_dir / "auth.json"
        self.tasks_file = self.config_dir / "tasks.json"
        self.logs_dir = self.config_dir / "logs"
        self.legacy_sessdata_file = Path("sessdata.txt")
        self.loaded_legacy_sessdata_file = False
        self._lock = threading.RLock()

    def load(self) -> AppConfig:
        config = AppConfig()

        if self.legacy_sessdata_file.exists() and not self.config_file.exists():
            config.sessdata = self.legacy_sessdata_file.read_text(encoding="utf-8").strip()
            self.loaded_legacy_sessdata_file = bool(config.sessdata)

        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            config = AppConfig(
                sessdata=_as_text(data.get("sessdata"), config.sessdata),
                save_path=_as_text(data.get("save_path"), config.save_path),
                organize_by_source=_as_bool(data.get("organize_by_source"), config.organize_by_source),
                filename_template=_as_text(data.get("filename_template"), config.filename_template)
                or config.filename_template,
                cleanup_temp_files=_as_bool(data.get("cleanup_temp_files"), config.cleanup_temp_files),
                steady_mode=_as_bool(data.get("steady_mode"), config.steady_mode),
                animations_enabled=_as_bool(data.get("animations_enabled"), config.animations_enabled),
                speed_limit_mb=_as_float(data.get("speed_limit_mb"), config.speed_limit_mb, minimum=0.0),
                max_concurrent_tasks=_as_int(
                    data.get("max_concurrent_tasks"),
                    config.max_concurrent_tasks,
                    minimum=1,
                    maximum=16,
                ),
                ffmpeg_path=_as_text(data.get("ffmpeg_path"), config.ffmpeg_path) or "ffmpeg",
                appearance_mode=_as_choice(
                    data.get("appearance_mode"),
                    config.appearance_mode,
                    {"system", "light", "dark"},
                ),
            )

        try:
            Path(config.save_path).mkdir(parents=True, exist_ok=True)
        except OSError:
            config.save_path = str(Path.home() / "Downloads" / "BiliDownloader")
            Path(config.save_path).mkdir(parents=True, exist_ok=True)
        self.save(config, preserve_legacy_sessdata=bool(config.sessdata))
        return config

    def save(self, config: AppConfig, preserve_legacy_sessdata: bool = False) -> None:
        with self._lock:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "save_path": config.save_path.strip(),
                "organize_by_source": bool(config.organize_by_source),
                "filename_template": config.filename_template.strip() or "{title}",
                "cleanup_temp_files": bool(config.cleanup_temp_files),
                "steady_mode": bool(config.steady_mode),
                "speed_limit_mb": float(config.speed_limit_mb),
                "max_concurrent_tasks": min(16, max(1, int(config.max_concurrent_tasks))),
                "ffmpeg_path": config.ffmpeg_path.strip() or "ffmpeg",
                "appearance_mode": config.appearance_mode.strip() or "system",
                "animations_enabled": bool(config.animations_enabled),
            }
            if preserve_legacy_sessdata and config.sessdata.strip():
                payload["sessdata"] = config.sessdata.strip()
            atomic_write_text(self.config_file, json.dumps(payload, ensure_ascii=False, indent=2))

    def remove_loaded_legacy_sessdata_file(self) -> None:
        if not self.loaded_legacy_sessdata_file:
            return
        try:
            self.legacy_sessdata_file.unlink(missing_ok=True)
        except OSError:
            return
        self.loaded_legacy_sessdata_file = False


def _as_text(value, default: str) -> str:
    if value is None:
        return default
    return str(value).strip()


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _as_float(value, default: float, minimum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    if minimum is not None and parsed < minimum:
        return default
    return parsed


def _as_int(value, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _as_choice(value, default: str, choices: set[str]) -> str:
    normalized = _as_text(value, default).lower()
    return normalized if normalized in choices else default
