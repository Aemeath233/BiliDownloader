from __future__ import annotations

import json
from pathlib import Path


class TaskStore:
    def __init__(self, tasks_file: Path) -> None:
        self.tasks_file = tasks_file

    def load(self) -> list[dict]:
        if not self.tasks_file.exists():
            return []

        try:
            data = json.loads(self.tasks_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def save(self, tasks: list[dict]) -> None:
        self.tasks_file.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(tasks, ensure_ascii=False, indent=2)
        self.tasks_file.write_text(payload, encoding="utf-8")
