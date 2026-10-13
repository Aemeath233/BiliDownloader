from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from bili_client.core.models import AppConfig, EpisodeInfo, QualityOption, StreamInfo, TaskStatus
from bili_client.core.naming import render_filename_template
from bili_client.services.auth_service import AuthService, AuthSession
from bili_client.services.bili_api import BilibiliApi
from bili_client.services.download_manager import DownloadManager
from bili_client.services.ffmpeg import FFmpegService


class AuthConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = AuthService(Mock(), Path(self.directory.name) / "auth.json")
        self.original = AuthSession(cookies={"SESSDATA": "old-test-session"})
        self.service.save_session(self.original)

    def test_reading_account_state_does_not_wait_for_network_refresh(self):
        entered, release = threading.Event(), threading.Event()

        def slow_info(_session):
            entered.set()
            release.wait(2)
            return {"code": 0, "data": {"refresh": False}}

        self.service._cookie_info = slow_info
        worker = threading.Thread(target=self.service.ensure_latest_session)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            started = time.monotonic()
            self.assertIs(self.original, self.service.load_session())
            self.assertLess(time.monotonic() - started, 0.1)
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())

    def test_old_refresh_cannot_overwrite_new_qr_account(self):
        new = AuthSession(cookies={"SESSDATA": "new-test-session"})
        self.service.save_session(new)
        returned = self.service._save_if_current(self.original, self.original)
        self.assertIs(new, returned)
        self.assertIs(new, self.service.load_session())

    def test_manual_session_changes_and_clear_are_applied(self):
        self.service.set_sessdata("manual-test-session")
        self.assertEqual("manual-test-session", self.service.load_session().sessdata)
        self.service.set_sessdata("")
        self.assertIsNone(self.service.load_session())
        self.assertFalse(self.service.auth_file.exists())
        self.assertEqual(2, self.service.http_client.clear_cookies.call_count)

    def test_invalid_timestamps_cannot_break_account_display(self):
        session = AuthSession.from_json({"updated_at": float("nan"), "last_checked_at": "bad"})
        self.assertEqual(0, session.updated_at)
        self.assertEqual(0, session.last_checked_at)


class DownloadRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.manager = DownloadManager(Mock(), Mock(), AppConfig(), logger=lambda _message: None)
        self.manager.shutdown()
        self.manager._stop_event.clear()
        self.task_id = self.manager.create_task(
            "https://www.bilibili.com/video/BV1xx411c7mD",
            EpisodeInfo("demo", "BV1xx411c7mD", 1, False),
            QualityOption(80, "1080P"),
            self.directory.name,
            "",
        )
        self.manager._set_totals(self.task_id, 10, 5)
        task = self.manager._tasks[self.task_id]
        task.progress.audio_downloaded_bytes = 5
        self.output = Path(self.directory.name) / "stream.m4s"

    def tearDown(self):
        self.manager.shutdown()

    def test_backup_replaces_partial_file_and_resets_only_its_progress(self):
        class Response:
            def __init__(self, fail):
                self.fail = fail

            def iter_bytes(self, chunk_size):
                if self.fail:
                    yield b"bad"
                    raise OSError("primary failed")
                yield b"0123456789"

        @contextmanager
        def stream(url, **_kwargs):
            yield Response(url.endswith("primary"))

        self.manager.api.http_client.stream = stream
        errors = []
        self.manager._download_stream(
            self.task_id,
            StreamInfo("video", "https://primary", 10, ["https://backup"]),
            "",
            self.output,
            errors,
            threading.Event(),
            threading.Event(),
        )
        progress = self.manager._tasks[self.task_id].progress
        self.assertEqual(b"0123456789", self.output.read_bytes())
        self.assertEqual(10, progress.video_downloaded_bytes)
        self.assertEqual(5, progress.audio_downloaded_bytes)
        self.assertEqual(15, progress.downloaded_bytes)
        self.assertEqual([], errors)

    def test_truncated_successful_response_is_not_accepted_as_complete(self):
        @contextmanager
        def stream(*_args, **_kwargs):
            response = Mock()
            response.iter_bytes.return_value = iter([b"short"])
            yield response

        self.manager.api.http_client.stream = stream
        errors, stop = [], threading.Event()
        self.manager._download_stream(
            self.task_id, StreamInfo("video", "https://primary", 10), "", self.output, errors, threading.Event(), stop
        )
        self.assertTrue(errors)
        self.assertTrue(stop.is_set())

    def test_temp_directory_failure_releases_active_task(self):
        self.manager._active_ids.add(self.task_id)
        self.manager._queue.remove(self.task_id)
        with patch.object(Path, "mkdir", side_effect=OSError("permission denied")):
            self.manager._run_task(self.task_id)
        self.assertEqual(TaskStatus.FAILED, self.manager._tasks[self.task_id].status)
        self.assertNotIn(self.task_id, self.manager._active_ids)

    def test_invalid_persisted_task_id_is_rejected(self):
        self.assertIsNone(self.manager._task_from_payload({"task_id": "../../outside"}))


class SourceAndCacheTests(unittest.TestCase):
    def test_source_ffmpeg_lookup_uses_only_path(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "ffmpeg.exe"
            binary.touch()
            with (
                patch.dict(os.environ, {"PATH": directory}),
                patch("bili_client.services.ffmpeg.os.name", "nt"),
                patch("bili_client.services.ffmpeg.sys.frozen", False, create=True),
                patch.object(FFmpegService, "_bundled_candidates", side_effect=AssertionError("must not scan")),
            ):
                self.assertEqual(str(binary), FFmpegService.resolve_command())

    def test_source_does_not_treat_working_directory_as_path(self):
        with (
            patch.dict(os.environ, {"PATH": ""}),
            patch("bili_client.services.ffmpeg.sys.frozen", False, create=True),
            patch.object(FFmpegService, "_bundled_candidates", side_effect=AssertionError("must not scan")),
        ):
            self.assertFalse(FFmpegService().is_available())

    def test_metadata_and_redirect_caches_are_bounded(self):
        api = BilibiliApi(Mock())
        api.MAX_CACHE_ENTRIES = 3
        for index in range(10):
            api._set_json_cache(("guest", "", str(index)), {"index": index}, 60)
            api._set_resolved_url_cache(("guest", str(index)), str(index), 60)
        self.assertEqual(3, len(api._json_cache))
        self.assertEqual(3, len(api._resolved_url_cache))
        self.assertEqual({"index": 9}, api._get_json_cache(("guest", "", "9")))

    def test_filename_template_cannot_request_unbounded_padding(self):
        name = render_filename_template(
            "{title:1000000000}",
            media_title="demo",
            episode=EpisodeInfo("demo", "BV1xx411c7mD", 1, False),
            quality=QualityOption(80, "1080P"),
            index=1,
            total=1,
        )
        self.assertEqual("demo", name)
