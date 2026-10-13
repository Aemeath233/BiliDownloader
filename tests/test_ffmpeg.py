from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from bili_client.services.ffmpeg import FFmpegService


class _FakeProcess:
    def __init__(self, command: list[str], *, returncode: int = 0, stderr: str = "") -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        self.terminated = False
        Path(command[-1]).write_bytes(b"merged")

    def communicate(self, timeout=None):
        return None, self.stderr

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.returncode = -9


class _CancelableProcess(_FakeProcess):
    def __init__(self, command: list[str]) -> None:
        super().__init__(command)
        self.calls = 0

    def communicate(self, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise subprocess.TimeoutExpired(self.command, timeout)
        return None, ""


class FFmpegServiceTests(unittest.TestCase):
    def test_onefile_uses_extracted_ffmpeg_without_system_installation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            extracted = Path(temp_dir) / "portable runtime"
            executable = extracted / "ffmpeg" / "ffmpeg.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"fixture")
            with (
                patch.object(sys, "_MEIPASS", str(extracted), create=True),
                patch.object(sys, "frozen", True, create=True),
                patch.object(FFmpegService, "_find_on_path", return_value=None),
            ):
                service = FFmpegService()
                self.assertEqual(str(executable), service.ffmpeg_path)
                self.assertTrue(service.is_available())

    def test_merge_replaces_final_file_only_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "output.mp4"
            service = FFmpegService("ffmpeg")
            with patch(
                "bili_client.services.ffmpeg.subprocess.Popen",
                side_effect=lambda command, **_kwargs: _FakeProcess(command),
            ):
                service.merge(root / "video.m4s", None, output)

            self.assertEqual(b"merged", output.read_bytes())
            self.assertFalse(any(root.glob("*.part.mp4")))

    def test_merge_cleans_partial_output_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "output.mp4"
            service = FFmpegService("ffmpeg")
            with patch(
                "bili_client.services.ffmpeg.subprocess.Popen",
                side_effect=lambda command, **_kwargs: _FakeProcess(command, returncode=1, stderr="bad input"),
            ):
                with self.assertRaisesRegex(RuntimeError, "bad input"):
                    service.merge(root / "video.m4s", None, output)

            self.assertFalse(output.exists())
            self.assertFalse(any(root.iterdir()))

    def test_merge_can_cancel_while_draining_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "output.mp4"
            cancel_event = threading.Event()
            cancel_event.set()
            process_holder: list[_CancelableProcess] = []

            def make_process(command, **_kwargs):
                process = _CancelableProcess(command)
                process_holder.append(process)
                return process

            with patch("bili_client.services.ffmpeg.subprocess.Popen", side_effect=make_process):
                with self.assertRaisesRegex(RuntimeError, "已取消"):
                    FFmpegService("ffmpeg").merge(root / "video.m4s", None, output, cancel_event)

            self.assertTrue(process_holder[0].terminated)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
