from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bili_client.core.models import AppConfig, DownloadMode, EpisodeInfo, QualityOption, StreamInfo, TaskStatus
from bili_client.services.bili_api import BiliApiError, BilibiliApi, QualityUnavailableError
from bili_client.services.config_store import ConfigStore
from bili_client.services.download_manager import DownloadManager
from tests.test_bili_api import DummyHttpClient, StubBiliApi, StubDurlBiliApi


class DownloadModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.episode = EpisodeInfo("demo", "BV1xx411c7mD", 1, False)

    def manager(self):
        api, ffmpeg = Mock(), Mock()
        api.get_streams.side_effect = lambda *args, **kwargs: (
            StreamInfo("video", "video", 5),
            None if kwargs.get("include_audio") is False else StreamInfo("audio", "audio", 3),
        )
        api.get_audio_stream.return_value = StreamInfo("audio", "audio", 3)
        manager = DownloadManager(api, ffmpeg, AppConfig())
        manager.shutdown()
        return manager

    def create(self, manager, mode, qn=80):
        return manager.create_task(
            "source", self.episode, QualityOption(qn, "quality"), self.temp.name, "", download_mode=mode
        )

    def test_only_requested_streams_are_downloaded_and_progress_counts_selected_bytes(self):
        for mode, kinds, size in (
            (DownloadMode.COMBINED, {"audio", "video"}, 8),
            (DownloadMode.AUDIO, {"audio"}, 3),
            (DownloadMode.VIDEO, {"video"}, 5),
        ):
            with self.subTest(mode=mode):
                manager = self.manager()
                task_id = self.create(manager, mode)
                downloaded = []

                def download(_id, stream, _sessdata, path, *_args, downloaded=downloaded):
                    downloaded.append(stream.kind)
                    path.write_bytes(b"data")

                manager._download_stream = download
                manager._stop_event.clear()
                manager._run_task(task_id)
                manager._stop_event.set()
                task = manager._tasks[task_id]
                self.assertEqual(TaskStatus.COMPLETED, task.status)
                self.assertEqual(kinds, set(downloaded))
                self.assertEqual(size, task.progress.total_bytes)
                self.assertEqual(mode.extension, Path(task.output_path).suffix)
                if mode == DownloadMode.AUDIO:
                    manager.api.get_streams.assert_not_called()
                else:
                    manager.api.get_audio_stream.assert_not_called()
                self.assertEqual(
                    None if mode == DownloadMode.COMBINED else mode.value,
                    manager.ffmpeg.merge.call_args.kwargs.get("track"),
                )

    def test_duplicate_identity_includes_mode_and_retries_preserve_mode(self):
        manager = self.manager()
        for mode in DownloadMode:
            task_id = self.create(manager, mode)
            self.assertEqual(1, manager.count_duplicates([self.episode], 80, mode))
            task = manager._tasks[task_id]
            task.status = TaskStatus.FAILED
            manager._queue.remove(task_id)
            single = manager.retry_task(task_id)
            self.assertEqual(mode, manager._tasks[single].download_mode)
            self.assertEqual(mode, manager.get_task_spec(task_id).download_mode)
        manager.retry_terminal_tasks()
        for task in manager._tasks.values():
            self.assertEqual(task.download_mode.extension, Path(task.output_path).suffix)

    def test_task_roundtrip_and_legacy_tasks_keep_their_mode(self):
        manager = self.manager()
        for mode in DownloadMode:
            task = manager._tasks[self.create(manager, mode)]
            payload = manager._task_to_payload(task)
            restored = manager._task_from_payload(payload)
            self.assertEqual(mode, restored.download_mode)
            self.assertEqual(task.output_path, restored.output_path)
        del payload["download_mode"]
        self.assertEqual(DownloadMode.COMBINED, manager._task_from_payload(payload).download_mode)
        payload["download_mode"] = "invalid"
        self.assertIsNone(manager._task_from_payload(payload))

    def test_audio_selection_does_not_require_or_probe_video(self):
        api = StubBiliApi(DummyHttpClient())
        with patch.object(api, "_probe_size", return_value=42) as probe:
            options = api.get_audio_quality_options(self.episode)
            self.assertEqual([30280], [option.qn for option in options])
            audio = api.get_audio_stream(self.episode, options[0].qn)
            self.assertEqual("audio", audio.kind)
            self.assertEqual("https://audio-primary", probe.call_args.args[0])
            self.assertEqual(1, probe.call_count)
            probe.reset_mock()
            _, audio = api.get_streams(self.episode, 80, include_audio=False)
            self.assertIsNone(audio)
            self.assertEqual("https://video-primary", probe.call_args.args[0])
            self.assertEqual(1, probe.call_count)
            with self.assertRaises(QualityUnavailableError):
                api.get_audio_stream(self.episode, 99999)

    def test_audio_options_use_only_available_tracks_including_flac_and_dolby(self):
        api = BilibiliApi(DummyHttpClient())
        payload = {
            "data": {
                "dash": {
                    "audio": [{"id": 1, "baseUrl": "https://a", "bandwidth": 64000}, {"id": 2}],
                    "flac": {"audio": {"id": 3, "base_url": "https://f", "bandwidth": 1000000}},
                    "dolby": {"audio": [{"id": 4, "baseUrl": "https://d", "bandwidth": 256000}]},
                }
            }
        }
        with patch.object(api, "_playurl", return_value=payload):
            self.assertEqual([3, 4, 1], [option.qn for option in api.get_audio_quality_options(self.episode)])
        with patch.object(api, "_playurl", return_value={"data": {"dash": {"video": []}}}):
            with self.assertRaises(BiliApiError):
                api.get_audio_quality_options(self.episode)
        with patch.object(api, "_playurl", return_value={"data": {"is_preview": True}}):
            with self.assertRaises(BiliApiError):
                api.get_audio_quality_options(self.episode)

    def test_legacy_muxed_audio_is_explicit_and_uses_backup_urls(self):
        api = StubDurlBiliApi(DummyHttpClient())
        option = api.get_audio_quality_options(self.episode)[0]
        self.assertIn("合流", option.availability_note)
        audio = api.get_audio_stream(self.episode, option.qn)
        self.assertEqual("audio", audio.kind)
        self.assertTrue(audio.backup_urls)

    def test_default_mode_configuration_roundtrip_and_invalid_value_fallback(self):
        with patch.dict("os.environ", {"APPDATA": self.temp.name}):
            store = ConfigStore()
            store.save(AppConfig(save_path=self.temp.name, default_download_mode="audio"))
            self.assertEqual("audio", store.load().default_download_mode)
            store.config_file.write_text('{"default_download_mode":"invalid"}', encoding="utf-8")
            self.assertEqual("combined", store.load().default_download_mode)


if __name__ == "__main__":
    unittest.main()
