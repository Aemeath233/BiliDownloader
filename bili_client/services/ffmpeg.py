from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path


class FFmpegService:
    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        self.ffmpeg_path = self.resolve_command(ffmpeg_path)

    def set_command(self, ffmpeg_path: str) -> None:
        self.ffmpeg_path = self.resolve_command(ffmpeg_path)

    def is_available(self) -> bool:
        if Path(self.ffmpeg_path).is_absolute() or Path(self.ffmpeg_path).parent != Path("."):
            return Path(self.ffmpeg_path).is_file()
        return self._find_on_path(self.ffmpeg_path) is not None

    @classmethod
    def resolve_command(cls, ffmpeg_path: str = "ffmpeg") -> str:
        candidate = (ffmpeg_path or "ffmpeg").strip() or "ffmpeg"
        if candidate.lower() not in {"ffmpeg", "ffmpeg.exe"}:
            return candidate

        if getattr(sys, "frozen", False):
            for bundled_path in cls._bundled_candidates():
                if bundled_path.is_file():
                    return str(bundled_path)

        return cls._find_on_path(candidate) or candidate

    @staticmethod
    def _find_on_path(command: str) -> str | None:
        names = (command,) if Path(command).suffix or os.name != "nt" else (command + ".exe",)
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            directory = Path(entry.strip('"'))
            if not entry or not directory.is_absolute():
                continue
            for name in names:
                executable = directory / name
                if executable.is_file() and (os.name == "nt" or os.access(executable, os.X_OK)):
                    return str(executable)
        return None

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

        return candidates

    def merge(
        self,
        video_path: Path,
        audio_path: Path | None,
        output_path: Path,
        cancel_event: threading.Event | None = None,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = output_path.with_name(
            f".{output_path.stem}.{uuid.uuid4().hex}.part{output_path.suffix or '.mp4'}"
        )
        command = [self.ffmpeg_path, "-nostdin", "-i", str(video_path)]
        if audio_path is not None:
            command += ["-i", str(audio_path)]
        command += ["-c", "copy", "-y", "-loglevel", "error", str(temporary_output)]

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                **self._hidden_process_options(),
            )
            stderr = ""
            while True:
                try:
                    _stdout, stderr = process.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    if cancel_event is not None and cancel_event.is_set():
                        process.terminate()
                        try:
                            _stdout, stderr = process.communicate(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            _stdout, stderr = process.communicate()
                        raise RuntimeError("已取消") from None

            if process.returncode != 0:
                raise RuntimeError((stderr or "").strip() or "FFmpeg 合并失败")
            os.replace(temporary_output, output_path)
        except Exception:
            try:
                temporary_output.unlink(missing_ok=True)
            except OSError:
                pass
            raise

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
