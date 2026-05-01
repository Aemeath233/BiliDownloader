from __future__ import annotations

import time
import tempfile
import unittest
from pathlib import Path

from bili_client.core.models import AppConfig, EpisodeInfo, QualityOption, StreamInfo, TaskStatus
from bili_client.services.bili_api import BiliApiError
from bili_client.services.download_manager import DownloadManager
from bili_client.services.task_store import TaskStore


class FakeApi:
    pass


class FlakyStreamApi:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_streams(self, episode: EpisodeInfo, qn: int, sessdata: str = ""):
        self.calls.append(sessdata)
        if len(self.calls) == 1:
            raise BiliApiError("login required")
        return StreamInfo(kind="video", url="https://video"), None


class FakeFFmpeg:
    def __init__(self) -> None:
        self.command = "ffmpeg"

    def set_command(self, ffmpeg_path: str) -> None:
        self.command = ffmpeg_path


class DownloadManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = DownloadManager(
            api=FakeApi(),  # type: ignore[arg-type]
            ffmpeg=FakeFFmpeg(),  # type: ignore[arg-type]
            config=AppConfig(),
            logger=lambda _message: None,
        )
        self.manager.shutdown()

    def tearDown(self) -> None:
        self.manager.shutdown()
        self.temp_dir.cleanup()

    def test_retry_failed_task_creates_new_queued_task(self) -> None:
        episode = EpisodeInfo(title="demo", bvid="BV1xx411c7mD", cid=1, is_bangumi=False)
        quality = QualityOption(qn=80, label="1080P")
        original_id = self.manager.create_task(
            source_url="https://www.bilibili.com/video/BV1xx411c7mD",
            episode=episode,
            quality=quality,
            save_dir=self.temp_dir.name,
            sessdata="",
        )

        with self.manager._tasks_lock:
            self.manager._queue.remove(original_id)
            task = self.manager._tasks[original_id]
            task.status = TaskStatus.FAILED
            task.finished_at = time.time()

        retried = self.manager.retry_terminal_tasks({TaskStatus.FAILED})

        snapshot = self.manager.snapshot()
        queued_tasks = [task for task in snapshot["tasks"] if task["status"] == TaskStatus.QUEUED.value]

        self.assertEqual(1, retried)
        self.assertEqual(1, len(queued_tasks))
        self.assertNotEqual(original_id, queued_tasks[0]["task_id"])

    def test_retry_single_task_creates_new_queued_task(self) -> None:
        episode = EpisodeInfo(title="demo", bvid="BV1xx411c7mD", cid=1, is_bangumi=False)
        quality = QualityOption(qn=80, label="1080P")
        original_id = self.manager.create_task(
            source_url="https://www.bilibili.com/video/BV1xx411c7mD",
            episode=episode,
            quality=quality,
            save_dir=self.temp_dir.name,
            sessdata="",
        )

        with self.manager._tasks_lock:
            self.manager._queue.remove(original_id)
            task = self.manager._tasks[original_id]
            task.status = TaskStatus.FAILED
            task.finished_at = time.time()

        new_task_id = self.manager.retry_task(original_id)

        snapshot = self.manager.snapshot()
        queued_tasks = [task for task in snapshot["tasks"] if task["status"] == TaskStatus.QUEUED.value]

        self.assertIsNotNone(new_task_id)
        self.assertEqual(1, len(queued_tasks))
        self.assertEqual(new_task_id, queued_tasks[0]["task_id"])

    def test_remove_terminal_task(self) -> None:
        episode = EpisodeInfo(title="demo", bvid="BV1xx411c7mD", cid=1, is_bangumi=False)
        quality = QualityOption(qn=80, label="1080P")
        task_id = self.manager.create_task(
            source_url="https://www.bilibili.com/video/BV1xx411c7mD",
            episode=episode,
            quality=quality,
            save_dir=self.temp_dir.name,
            sessdata="",
        )

        with self.manager._tasks_lock:
            self.manager._queue.remove(task_id)
            task = self.manager._tasks[task_id]
            task.status = TaskStatus.CANCELED
            task.finished_at = time.time()

        removed = self.manager.remove_task(task_id)

        self.assertTrue(removed)
        self.assertNotIn(task_id, {task["task_id"] for task in self.manager.snapshot()["tasks"]})

    def test_clear_finished_removes_only_terminal_tasks(self) -> None:
        episode = EpisodeInfo(title="demo", bvid="BV1xx411c7mD", cid=1, is_bangumi=False)
        quality = QualityOption(qn=80, label="1080P")
        completed_id = self.manager.create_task(
            source_url="https://www.bilibili.com/video/BV1xx411c7mD",
            episode=episode,
            quality=quality,
            save_dir=self.temp_dir.name,
            sessdata="",
        )
        queued_id = self.manager.create_task(
            source_url="https://www.bilibili.com/video/BV1xx411c7mD?p=2",
            episode=episode,
            quality=quality,
            save_dir=self.temp_dir.name,
            sessdata="",
        )

        with self.manager._tasks_lock:
            self.manager._queue.remove(completed_id)
            task = self.manager._tasks[completed_id]
            task.status = TaskStatus.COMPLETED
            task.finished_at = time.time()

        cleared = self.manager.clear_finished({TaskStatus.COMPLETED})

        snapshot = self.manager.snapshot()
        task_ids = {task["task_id"] for task in snapshot["tasks"]}

        self.assertEqual(1, cleared)
        self.assertNotIn(completed_id, task_ids)
        self.assertIn(queued_id, task_ids)

    def test_high_quality_stream_failure_refreshes_sessdata_and_retries_once(self) -> None:
        api = FlakyStreamApi()
        manager = DownloadManager(
            api=api,  # type: ignore[arg-type]
            ffmpeg=FakeFFmpeg(),  # type: ignore[arg-type]
            config=AppConfig(),
            logger=lambda _message: None,
            auth_refresher=lambda force: "fresh-sess" if force else "old-sess",
        )
        manager.shutdown()
        self.addCleanup(manager.shutdown)

        episode = EpisodeInfo(title="demo", bvid="BV1xx411c7mD", cid=1, is_bangumi=False)
        quality = QualityOption(qn=80, label="1080P")
        task_id = manager.create_task(
            source_url="https://www.bilibili.com/video/BV1xx411c7mD",
            episode=episode,
            quality=quality,
            save_dir=self.temp_dir.name,
            sessdata="old-sess",
        )
        with manager._tasks_lock:
            manager._queue.remove(task_id)
            task = manager._tasks[task_id]

        video_stream, audio_stream, sessdata = manager._get_streams_for_task(task_id, task)

        self.assertEqual("https://video", video_stream.url)
        self.assertIsNone(audio_stream)
        self.assertEqual("fresh-sess", sessdata)
        self.assertEqual(["old-sess", "fresh-sess"], api.calls)

    def test_persisted_unfinished_task_restores_as_retryable_canceled(self) -> None:
        store = TaskStore(Path(self.temp_dir.name) / "tasks.json")
        manager = DownloadManager(
            api=FakeApi(),  # type: ignore[arg-type]
            ffmpeg=FakeFFmpeg(),  # type: ignore[arg-type]
            config=AppConfig(),
            logger=lambda _message: None,
            task_store=store,
        )
        manager.shutdown()
        self.addCleanup(manager.shutdown)

        episode = EpisodeInfo(title="demo", bvid="BV1xx411c7mD", cid=1, is_bangumi=False)
        quality = QualityOption(qn=80, label="1080P")
        task_id = manager.create_task(
            source_url="https://www.bilibili.com/video/BV1xx411c7mD",
            episode=episode,
            quality=quality,
            save_dir=self.temp_dir.name,
            sessdata="sess",
        )

        restored = DownloadManager(
            api=FakeApi(),  # type: ignore[arg-type]
            ffmpeg=FakeFFmpeg(),  # type: ignore[arg-type]
            config=AppConfig(),
            logger=lambda _message: None,
            task_store=store,
        )
        restored.shutdown()
        self.addCleanup(restored.shutdown)

        tasks = {task["task_id"]: task for task in restored.snapshot()["tasks"]}

        self.assertIn(task_id, tasks)
        self.assertEqual(TaskStatus.CANCELED.value, tasks[task_id]["status"])
        self.assertTrue(tasks[task_id]["can_retry"])
        self.assertIn("上次退出", tasks[task_id]["message"])


if __name__ == "__main__":
    unittest.main()
