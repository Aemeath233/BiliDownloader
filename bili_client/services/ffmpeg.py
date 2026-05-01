from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path


class FFmpegService:
    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        self.ffmpeg_path = self.resolve_command(ffmpeg_path)

    def set_command(self, ffmpeg_path: str) -> None:
        self.ffmpeg_path = self.resolve_command(ffmpeg_path)

    def is_available(self) -> bool:
        return bool(shutil.which(self.ffmpeg_path) or Path(self.ffmpeg_path).exists())

    @classmethod
    def resolve_command(cls, ffmpeg_path: str = "ffmpeg") -> str:
        candidate = (ffmpeg_path or "ffmpeg").strip() or "ffmpeg"
        if candidate.lower() not in {"ffmpeg", "ffmpeg.exe"}:
            return candidate

        for bundled_path in cls._bundled_candidates():
            if bundled_path.exists():
                return str(bundled_path)

        return shutil.which(candidate) or candidate

    @staticmethod
    def _bundled_candidates() -> list[Path]:
        candidates: list[Path] = []

        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "ffmpeg" / "ffmpeg.exe")

        if getattr(sys, "frozen", False):
            executable_dir = Path(sys.executable).resolve().parent
            candidates.extend(
                [
                    executable_dir / "_internal" / "ffmpeg" / "ffmpeg.exe",
                    executable_dir / "ffmpeg" / "ffmpeg.exe",
                ]
            )

        project_root = Path(__file__).resolve().parents[2]
        candidates.append(project_root / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe")
        return candidates

    def merge(
        self,
        video_path: Path,
        audio_path: Path | None,
        output_path: Path,
        cancel_event: threading.Event | None = None,
    ) -> None:
        command = [self.ffmpeg_path, "-i", str(video_path)]
        if audio_path is not None:
            command += ["-i", str(audio_path)]
        command += ["-c", "copy", "-y", "-loglevel", "error", str(output_path)]

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **self._hidden_process_options(),
        )
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise RuntimeError("已取消")
            time.sleep(0.1)

        stdout, stderr = process.communicate()
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(stderr or "FFmpeg 合并失败")

    @staticmethod
    def _hidden_process_options() -> dict:
        if os.name != "nt":
            return {}

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        return {
            "creationflags": subprocess.CREATE_NO_WINDOW,
            "startupinfo": startupinfo,
        }
