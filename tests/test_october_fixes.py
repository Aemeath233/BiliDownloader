from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bili_client.core.models import AppConfig, EpisodeInfo, QualityOption, TaskStatus
from bili_client.services.auth_service import AuthService, AuthSession
from bili_client.services.download_manager import DownloadManager
from bili_client.services.ffmpeg import FFmpegService
from bili_client.services.task_store import TaskStore
from bili_client.services.task_writer import TaskWriter


class ReliabilityTests(unittest.TestCase):
    def test_logout_invalidates_a_refresh_that_is_already_in_flight(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AuthService(Mock(), Path(directory) / "auth.json")
            service.save_session(AuthSession(cookies={"SESSDATA": "test-old"}))
            entered, release = threading.Event(), threading.Event()

            def slow_info(_session):
                entered.set()
                release.wait(3)
                return {"code": 0, "data": {"refresh": False}}

            service._cookie_info = slow_info
            results = []
            worker = threading.Thread(target=lambda: results.append(service.ensure_latest_session()))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                service.set_sessdata("")
            finally:
                release.set()
                worker.join(3)
            self.assertEqual([None], results)
            self.assertIsNone(service.load_session())
            self.assertFalse(service.auth_file.exists())
            self.assertIsNone(service.ensure_latest_session("test-old"))

    def test_publish_keeps_files_created_while_download_was_running(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, partial = root / "movie.mp4", root / "temporary.mp4"
            output.write_bytes(b"user-file")
            (root / "movie (1).mp4").write_bytes(b"another-user-file")
            partial.write_bytes(b"downloaded")
            actual = FFmpegService._publish_output(partial, output)
            self.assertEqual(root / "movie (2).mp4", actual)
            self.assertEqual(b"user-file", output.read_bytes())
            self.assertEqual(b"another-user-file", (root / "movie (1).mp4").read_bytes())
            self.assertEqual(b"downloaded", actual.read_bytes())

    def test_cancel_after_ffmpeg_finishes_does_not_publish_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cancel = threading.Event()

            def start(command, **_kwargs):
                Path(command[-1]).write_bytes(b"media")
                cancel.set()
                process = Mock(returncode=0)
                process.communicate.return_value = (None, "")
                return process

            with patch("bili_client.services.ffmpeg.subprocess.Popen", start):
                with self.assertRaisesRegex(RuntimeError, "已取消"):
                    FFmpegService().merge(root / "input.m4s", None, root / "out.mp4", cancel)
            self.assertEqual([], list(root.iterdir()))

    def test_writer_orders_newer_state_after_a_slow_write(self):
        entered, release = threading.Event(), threading.Event()
        state, writes = [1], []

        def save():
            value = state[0]
            if value == 1:
                entered.set()
                release.wait(3)
            writes.append(value)

        writer = TaskWriter(save, Mock())
        writer.request()
        try:
            self.assertTrue(entered.wait(1))
            state[0] = 2
            for _ in range(20):
                writer.request()
        finally:
            release.set()
        writer.flush()
        self.assertEqual([1, 2], writes)

    def test_writer_error_is_visible_and_later_save_can_recover(self):
        save = Mock(side_effect=OSError("disk full"))
        writer = TaskWriter(save, Mock())
        writer.request()
        with self.assertRaisesRegex(OSError, "disk full"):
            writer.flush()
        self.assertIn("disk full", writer.error)
        save.side_effect = None
        writer.request()
        writer.flush()
        self.assertEqual("", writer.error)

    def test_disk_write_does_not_hold_task_lock_or_change_download_status(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.json")
            manager = DownloadManager(Mock(), Mock(), AppConfig(), task_store=store)
            manager.shutdown()
            entered, release = threading.Event(), threading.Event()

            def slow_write(_payload):
                entered.set()
                release.wait(3)

            with patch.object(store, "save", slow_write):
                task_id = manager.create_task(
                    "https://example",
                    EpisodeInfo("test", "BV1xx411c7mD", 1, False),
                    QualityOption(80, "1080P"),
                    directory,
                    "",
                )
                try:
                    self.assertTrue(entered.wait(1))
                    acquired = manager._tasks_lock.acquire(timeout=0.1)
                    self.assertTrue(acquired, "task lock was held during disk IO")
                    if acquired:
                        manager._tasks_lock.release()
                    self.assertEqual(TaskStatus.QUEUED, manager._tasks[task_id].status)
                finally:
                    release.set()
                    manager.flush_persistence()

    def test_completed_download_records_the_final_conflict_free_filename(self):
        from test_download_manager import DownloadingApi, FakeFFmpeg

        class RenamingFFmpeg(FakeFFmpeg):
            def merge(self, video_path, audio_path, output_path, cancel_event=None):
                actual = output_path.with_stem(output_path.stem + " (1)")
                actual.write_bytes(video_path.read_bytes())
                return actual

        with tempfile.TemporaryDirectory() as directory:
            manager = DownloadManager(DownloadingApi(), RenamingFFmpeg(), AppConfig())
            manager.shutdown()
            manager._stop_event.clear()
            task_id = manager.create_task(
                "https://example",
                EpisodeInfo("movie", "BV1xx411c7mD", 1, False),
                QualityOption(80, "1080P"),
                directory,
                "",
            )
            manager._run_task(task_id)
            self.assertEqual(TaskStatus.COMPLETED, manager._tasks[task_id].status)
            self.assertEqual("movie (1).mp4", Path(manager._tasks[task_id].output_path).name)
            manager.shutdown()
