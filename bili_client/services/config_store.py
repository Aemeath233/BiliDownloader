from __future__ import annotations

import json
import os
from pathlib import Path

from bili_client.core.models import AppConfig


class ConfigStore:
    def __init__(self, app_name: str = "BiliDownloader") -> None:
        appdata = Path(os.getenv("APPDATA") or Path.home())
        self.config_dir = appdata / app_name
        self.config_file = self.config_dir / "config.json"
        self.auth_file = self.config_dir / "auth.json"
        self.tasks_file = self.config_dir / "tasks.json"
        self.logs_dir = self.config_dir / "logs"
        self.legacy_sessdata_file = Path("sessdata.txt")

    def load(self) -> AppConfig:
        config = AppConfig()

        if self.legacy_sessdata_file.exists() and not self.config_file.exists():
            config.sessdata = self.legacy_sessdata_file.read_text(encoding="utf-8").strip()

        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            config = AppConfig(
                sessdata=str(data.get("sessdata", config.sessdata)).strip(),
                save_path=str(data.get("save_path", config.save_path)).strip(),
                organize_by_source=bool(data.get("organize_by_source", config.organize_by_source)),
                filename_template=str(data.get("filename_template", config.filename_template)).strip()
                or config.filename_template,
                cleanup_temp_files=bool(data.get("cleanup_temp_files", config.cleanup_temp_files)),
                speed_limit_mb=float(data.get("speed_limit_mb", config.speed_limit_mb) or 0),
                max_concurrent_tasks=max(1, int(data.get("max_concurrent_tasks", config.max_concurrent_tasks) or 1)),
                ffmpeg_path=str(data.get("ffmpeg_path", config.ffmpeg_path)).strip() or "ffmpeg",
                appearance_mode=str(data.get("appearance_mode", config.appearance_mode)).strip() or "system",
            )

        Path(config.save_path).mkdir(parents=True, exist_ok=True)
        self.save(config)
        return config

    def save(self, config: AppConfig) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessdata": config.sessdata.strip(),
            "save_path": config.save_path.strip(),
            "organize_by_source": bool(config.organize_by_source),
            "filename_template": config.filename_template.strip() or "{title}",
            "cleanup_temp_files": bool(config.cleanup_temp_files),
            "speed_limit_mb": float(config.speed_limit_mb),
            "max_concurrent_tasks": int(config.max_concurrent_tasks),
            "ffmpeg_path": config.ffmpeg_path.strip() or "ffmpeg",
            "appearance_mode": config.appearance_mode.strip() or "system",
        }
        self.config_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
