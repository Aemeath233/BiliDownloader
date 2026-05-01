from __future__ import annotations

import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from bili_client.core.errors import humanize_error
from bili_client.core.models import (
    AppConfig,
    DownloadTask,
    EpisodeInfo,
    ProgressSnapshot,
    QualityOption,
    StreamInfo,
    TaskStatus,
)
from bili_client.services.bili_api import BiliApiError, BilibiliApi
from bili_client.services.ffmpeg import FFmpegService
from bili_client.services.rate_limiter import GlobalRateLimiter
from bili_client.services.task_store import TaskStore


TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}
RETRYABLE_STATUSES = {TaskStatus.FAILED, TaskStatus.CANCELED}


class DownloadCancelled(RuntimeError):
    pass


class DownloadManager:
    def __init__(
        self,
        api: BilibiliApi,
        ffmpeg: FFmpegService,
        config: AppConfig,
        logger: Callable[[str], None] | None = None,
        auth_refresher: Callable[[bool], str] | None = None,
        task_store: TaskStore | None = None,
    ) -> None:
        self.api = api
        self.ffmpeg = ffmpeg
        self.logger = logger or (lambda _message: None)
        self.auth_refresher = auth_refresher
        self.task_store = task_store

        self._config_lock = threading.RLock()
        self._tasks_lock = threading.RLock()
        self._tasks: dict[str, DownloadTask] = {}
        self._queue: list[str] = []
        self._active_ids: set[str] = set()
        self._workers: dict[str, threading.Thread] = {}
        self._stop_event = threading.Event()

        self._max_concurrent_tasks = max(1, int(config.max_concurrent_tasks))
        self._rate_limiter = GlobalRateLimiter(int(max(0.0, config.speed_limit_mb) * 1024 * 1024))
        self.ffmpeg.set_command(config.ffmpeg_path)

        self._load_persisted_tasks()

        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="download-scheduler")
        self._scheduler_thread.start()

    def set_auth_refresher(self, auth_refresher: Callable[[bool], str] | None) -> None:
        self.auth_refresher = auth_refresher

    def update_config(self, config: AppConfig) -> None:
        with self._config_lock:
            self._max_concurrent_tasks = max(1, int(config.max_concurrent_tasks))
            self._rate_limiter.update_limit(int(max(0.0, config.speed_limit_mb) * 1024 * 1024))
            self.ffmpeg.set_command(config.ffmpeg_path)
        self.logger(
            f"运行配置已更新：并发任务 {self._max_concurrent_tasks}，"
            f"全局限速 {config.speed_limit_mb or 0:.2f} MB/s"
        )

    def create_task(
        self,
        source_url: str,
        episode: EpisodeInfo,
        quality: QualityOption,
        save_dir: str,
        sessdata: str,
    ) -> str:
        task_id = uuid.uuid4().hex[:10]
        save_path = Path(save_dir)
        save_path.mkdir(parents=True, exist_ok=True)
        output_path = self._next_available_path(save_path / f"{episode.title}.mp4")

        task = DownloadTask(
            task_id=task_id,
            source_url=source_url,
            output_path=str(output_path),
            save_dir=str(save_path),
            sessdata=sessdata.strip(),
            episode=episode,
            quality=quality,
            title=episode.title,
        )

        with self._tasks_lock:
            self._tasks[task_id] = task
            self._queue.append(task_id)
            self._save_tasks_locked()

        self.logger(f"任务已加入队列：{task.title} [{quality.label}]")
        return task_id

    def cancel_task(self, task_id: str) -> bool:
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if task is None or task.status in TERMINAL_STATUSES or task.cancel_event.is_set():
                return False

            task.cancel_event.set()
            task.progress.speed_bps = 0.0
            title = task.title

            if task.status == TaskStatus.QUEUED:
                self._queue = [queued_id for queued_id in self._queue if queued_id != task_id]
                self._mark_canceled_locked(task)
                self._save_tasks_locked()
                self.logger(f"任务已取消：{title}")
                return True

            task.message = "取消中"
            self._save_tasks_locked()

        self.logger(f"已请求取消任务：{title}")
        return True

    def clear_finished(self, statuses: set[TaskStatus] | None = None) -> int:
        removable_statuses = statuses or TERMINAL_STATUSES
        with self._tasks_lock:
            removable_ids = [
                task_id
                for task_id, task in self._tasks.items()
                if task.status in removable_statuses and task_id not in self._active_ids and task_id not in self._queue
            ]
            for task_id in removable_ids:
                del self._tasks[task_id]
            if removable_ids:
                self._save_tasks_locked()

        if removable_ids:
            self.logger(f"已清理 {len(removable_ids)} 个已结束任务")
        return len(removable_ids)

    def retry_terminal_tasks(self, statuses: set[TaskStatus] | None = None) -> int:
        retry_statuses = statuses or RETRYABLE_STATUSES
        with self._tasks_lock:
            specs = [
                (
                    task.source_url,
                    task.episode,
                    task.quality,
                    task.save_dir,
                    task.sessdata,
                    task.title,
                )
                for task in self._tasks.values()
                if task.status in retry_statuses
            ]

        for source_url, episode, quality, save_dir, sessdata, _title in specs:
            self.create_task(
                source_url=source_url,
                episode=episode,
                quality=quality,
                save_dir=save_dir,
                sessdata=sessdata,
            )

        if specs:
            self.logger(f"已重新加入 {len(specs)} 个失败/取消任务")
        return len(specs)

    def retry_task(self, task_id: str) -> str | None:
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if task is None or task.status not in RETRYABLE_STATUSES:
                return None
            spec = (task.source_url, task.episode, task.quality, task.save_dir, task.sessdata, task.title)

        source_url, episode, quality, save_dir, sessdata, title = spec
        new_task_id = self.create_task(
            source_url=source_url,
            episode=episode,
            quality=quality,
            save_dir=save_dir,
            sessdata=sessdata,
        )
        self.logger(f"已重新加入任务：{title}")
        return new_task_id

    def remove_task(self, task_id: str) -> bool:
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if task is None or task_id in self._active_ids:
                return False

            if task.status == TaskStatus.QUEUED:
                self._queue = [queued_id for queued_id in self._queue if queued_id != task_id]
            elif task.status not in TERMINAL_STATUSES:
                return False

            title = task.title
            del self._tasks[task_id]
            self._save_tasks_locked()

        self.logger(f"已移除任务：{title}")
        return True

    def has_unfinished_tasks(self) -> bool:
        with self._tasks_lock:
            return any(task.status not in TERMINAL_STATUSES for task in self._tasks.values())

    def snapshot(self) -> dict:
        with self._tasks_lock:
            queue_positions = {task_id: index + 1 for index, task_id in enumerate(self._queue)}
            ordered_tasks = sorted(self._tasks.values(), key=lambda item: self._task_sort_key(item, queue_positions))
            tasks = [self._serialize_task(task, queue_positions.get(task.task_id)) for task in ordered_tasks]
            active_count = len(self._active_ids)
            queued_count = sum(1 for task in self._tasks.values() if task.status == TaskStatus.QUEUED)
            completed_count = sum(1 for task in self._tasks.values() if task.status == TaskStatus.COMPLETED)
            failed_count = sum(1 for task in self._tasks.values() if task.status == TaskStatus.FAILED)
            canceled_count = sum(1 for task in self._tasks.values() if task.status == TaskStatus.CANCELED)
            total_speed = sum(task.progress.speed_bps for task in self._tasks.values() if task.status == TaskStatus.DOWNLOADING)

        return {
            "tasks": tasks,
            "metrics": {
                "active_count": active_count,
                "queued_count": queued_count,
                "completed_count": completed_count,
                "failed_count": failed_count,
                "canceled_count": canceled_count,
                "total_speed_bps": total_speed,
                "speed_limit_bps": self._rate_limiter.get_limit(),
            },
        }

    def shutdown(self) -> None:
        if self._stop_event.is_set():
            return

        self._stop_event.set()
        with self._tasks_lock:
            queued_ids = list(self._queue)
            active_ids = list(self._active_ids)
            self._queue.clear()

            for task_id in queued_ids:
                task = self._tasks.get(task_id)
                if task is not None and task.status == TaskStatus.QUEUED:
                    task.cancel_event.set()
                    self._mark_canceled_locked(task, "程序退出")

            for task_id in active_ids:
                task = self._tasks.get(task_id)
                if task is not None and task.status not in TERMINAL_STATUSES:
                    task.cancel_event.set()
                    task.message = "程序退出，正在停止"

            workers = list(self._workers.values())
            self._save_tasks_locked()

        self._scheduler_thread.join(timeout=1.5)
        for worker in workers:
            worker.join(timeout=5.0)
        self._save_tasks()

    def _load_persisted_tasks(self) -> None:
        if self.task_store is None:
            return

        restored = 0
        dirty = False
        with self._tasks_lock:
            for payload in self.task_store.load():
                task = self._task_from_payload(payload)
                if task is None or task.task_id in self._tasks:
                    continue

                if task.status not in TERMINAL_STATUSES:
                    task.cancel_event.set()
                    self._mark_canceled_locked(task, "上次退出时未完成，可重试")
                    dirty = True

                self._tasks[task.task_id] = task
                restored += 1

            if dirty:
                self._save_tasks_locked()

        if restored:
            self.logger(f"已恢复 {restored} 个历史任务")

    def _save_tasks(self) -> None:
        with self._tasks_lock:
            self._save_tasks_locked()

    def _save_tasks_locked(self) -> None:
        if self.task_store is None:
            return
        tasks = sorted(self._tasks.values(), key=lambda item: item.created_at)
        self.task_store.save([self._task_to_payload(task) for task in tasks])

    def _task_to_payload(self, task: DownloadTask) -> dict:
        return {
            "task_id": task.task_id,
            "source_url": task.source_url,
            "output_path": task.output_path,
            "save_dir": task.save_dir,
            "sessdata": task.sessdata,
            "title": task.title,
            "status": task.status.value,
            "message": task.message,
            "error": task.error,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "episode": {
                "title": task.episode.title,
                "bvid": task.episode.bvid,
                "cid": task.episode.cid,
                "is_bangumi": task.episode.is_bangumi,
            },
            "quality": {
                "qn": task.quality.qn,
                "label": task.quality.label,
            },
            "progress": {
                "total_bytes": task.progress.total_bytes,
                "downloaded_bytes": task.progress.downloaded_bytes,
                "percent": task.progress.percent,
                "video_total_bytes": task.progress.video_total_bytes,
                "video_downloaded_bytes": task.progress.video_downloaded_bytes,
                "audio_total_bytes": task.progress.audio_total_bytes,
                "audio_downloaded_bytes": task.progress.audio_downloaded_bytes,
            },
        }

    def _task_from_payload(self, payload: dict) -> DownloadTask | None:
        try:
            title = str(payload.get("title") or "未命名任务")
            save_dir = str(payload.get("save_dir") or Path.cwd())
            output_path = str(payload.get("output_path") or Path(save_dir) / f"{title}.mp4")

            episode_payload = payload.get("episode") if isinstance(payload.get("episode"), dict) else {}
            quality_payload = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
            progress_payload = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}

            try:
                status = TaskStatus(str(payload.get("status") or TaskStatus.CANCELED.value))
            except ValueError:
                status = TaskStatus.CANCELED

            episode = EpisodeInfo(
                title=str(episode_payload.get("title") or title),
                bvid=str(episode_payload.get("bvid") or payload.get("bvid") or ""),
                cid=int(episode_payload.get("cid") or payload.get("cid") or 0),
                is_bangumi=bool(episode_payload.get("is_bangumi", payload.get("is_bangumi", False))),
            )
            quality = QualityOption(
                qn=int(quality_payload.get("qn") or payload.get("qn") or 0),
                label=str(quality_payload.get("label") or payload.get("quality_label") or "默认画质"),
            )
            progress = ProgressSnapshot(
                total_bytes=int(progress_payload.get("total_bytes") or payload.get("total_bytes") or 0),
                downloaded_bytes=int(progress_payload.get("downloaded_bytes") or payload.get("downloaded_bytes") or 0),
                percent=float(progress_payload.get("percent") or payload.get("percent") or 0.0),
                speed_bps=0.0,
                video_total_bytes=int(
                    progress_payload.get("video_total_bytes") or payload.get("video_total_bytes") or 0
                ),
                video_downloaded_bytes=int(
                    progress_payload.get("video_downloaded_bytes") or payload.get("video_downloaded_bytes") or 0
                ),
                audio_total_bytes=int(
                    progress_payload.get("audio_total_bytes") or payload.get("audio_total_bytes") or 0
                ),
                audio_downloaded_bytes=int(
                    progress_payload.get("audio_downloaded_bytes") or payload.get("audio_downloaded_bytes") or 0
                ),
            )

            started_at = float(payload["started_at"]) if payload.get("started_at") is not None else None
            finished_at = float(payload["finished_at"]) if payload.get("finished_at") is not None else None

            return DownloadTask(
                task_id=str(payload.get("task_id") or uuid.uuid4().hex[:10]),
                source_url=str(payload.get("source_url") or ""),
                output_path=output_path,
                save_dir=save_dir,
                sessdata=str(payload.get("sessdata") or ""),
                episode=episode,
                quality=quality,
                title=title,
                status=status,
                message=str(payload.get("message") or ""),
                error=str(payload.get("error") or ""),
                created_at=float(payload.get("created_at") or time.time()),
                started_at=started_at,
                finished_at=finished_at,
                progress=progress,
            )
        except (TypeError, ValueError):
            return None

    def _next_available_path(self, candidate: Path) -> Path:
        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix
        parent = candidate.parent
        counter = 1
        while True:
            next_path = parent / f"{stem} ({counter}){suffix}"
            if not next_path.exists():
                return next_path
            counter += 1

    def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            task_id = None
            with self._tasks_lock:
                if len(self._active_ids) < self._max_concurrent_tasks and self._queue:
                    task_id = self._queue.pop(0)
                    task = self._tasks.get(task_id)
                    if task and task.status == TaskStatus.QUEUED:
                        self._active_ids.add(task_id)
                    else:
                        task_id = None

            if task_id:
                worker = threading.Thread(target=self._run_task, args=(task_id,), daemon=True, name=f"download-{task_id}")
                with self._tasks_lock:
                    self._workers[task_id] = worker
                worker.start()
            else:
                time.sleep(0.2)

    def _run_task(self, task_id: str) -> None:
        with self._tasks_lock:
            task = self._tasks.get(task_id)
        if task is None:
            return

        cancel_event = task.cancel_event
        temp_dir = Path(task.save_dir) / ".bili_client" / task.task_id
        temp_dir.mkdir(parents=True, exist_ok=True)
        video_path = temp_dir / "video.m4s"
        audio_path = temp_dir / "audio.m4s"

        try:
            self._ensure_not_cancelled(cancel_event)
            self._set_status(task_id, TaskStatus.PREPARING, "解析流地址")
            video_stream, audio_stream, sessdata = self._get_streams_for_task(task_id, task)
            self._ensure_not_cancelled(cancel_event)
            self._set_totals(task_id, video_stream.size_bytes, audio_stream.size_bytes if audio_stream else 0)

            self._set_status(task_id, TaskStatus.DOWNLOADING, "下载中")

            errors: list[str] = []
            stop_event = threading.Event()

            video_worker = threading.Thread(
                target=self._download_stream,
                args=(task_id, video_stream, sessdata, video_path, errors, cancel_event, stop_event),
                daemon=True,
            )
            video_worker.start()

            audio_worker = None
            if audio_stream is not None:
                audio_worker = threading.Thread(
                    target=self._download_stream,
                    args=(task_id, audio_stream, sessdata, audio_path, errors, cancel_event, stop_event),
                    daemon=True,
                )
                audio_worker.start()

            video_worker.join()
            if audio_worker is not None:
                audio_worker.join()

            if errors:
                raise RuntimeError("; ".join(errors))
            self._ensure_not_cancelled(cancel_event)

            self._set_status(task_id, TaskStatus.MERGING, "FFmpeg 合并中")
            self.ffmpeg.merge(
                video_path=video_path,
                audio_path=audio_path if audio_stream else None,
                output_path=Path(task.output_path),
                cancel_event=cancel_event,
            )
            self._ensure_not_cancelled(cancel_event)

            with self._tasks_lock:
                task = self._tasks[task_id]
                task.progress.percent = 100.0
                task.progress.speed_bps = 0.0
                task.finished_at = time.time()
                task.status = TaskStatus.COMPLETED
                task.message = "下载完成"
                task._speed_samples.clear()
                self._save_tasks_locked()
            self.logger(f"任务完成：{task.title}")

        except DownloadCancelled as exc:
            self._cancel_active_task(task_id, str(exc) or "已取消")
        except BiliApiError as exc:
            if cancel_event.is_set() or self._stop_event.is_set():
                self._cancel_active_task(task_id, "程序退出" if self._stop_event.is_set() else "已取消")
            else:
                self._fail_task(task_id, str(exc))
        except Exception as exc:
            if cancel_event.is_set() or self._stop_event.is_set():
                self._cancel_active_task(task_id, "程序退出" if self._stop_event.is_set() else "已取消")
            else:
                self._fail_task(task_id, str(exc))
        finally:
            with self._tasks_lock:
                self._active_ids.discard(task_id)
                self._workers.pop(task_id, None)
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _get_streams_for_task(
        self,
        task_id: str,
        task: DownloadTask,
    ) -> tuple[StreamInfo, StreamInfo | None, str]:
        sessdata = self._refresh_task_sessdata(task_id, task, force=False)
        try:
            video_stream, audio_stream = self.api.get_streams(task.episode, task.quality.qn, sessdata)
            return video_stream, audio_stream, sessdata
        except BiliApiError:
            if task.quality.qn < 80 or self.auth_refresher is None:
                raise

            refreshed_sessdata = self._refresh_task_sessdata(task_id, task, force=True)
            if not refreshed_sessdata or refreshed_sessdata == sessdata:
                raise

            self.logger(f"高画质取流失败，已刷新登录态后重试一次：{task.title}")
            video_stream, audio_stream = self.api.get_streams(task.episode, task.quality.qn, refreshed_sessdata)
            return video_stream, audio_stream, refreshed_sessdata

    def _refresh_task_sessdata(self, task_id: str, task: DownloadTask, force: bool) -> str:
        if self.auth_refresher is None:
            return task.sessdata

        try:
            sessdata = self.auth_refresher(force).strip()
        except Exception as exc:
            self.logger(f"登录态检查失败：{exc}")
            return task.sessdata

        if not sessdata:
            return task.sessdata

        if sessdata != task.sessdata:
            with self._tasks_lock:
                current = self._tasks.get(task_id)
                if current is not None:
                    current.sessdata = sessdata
                    self._save_tasks_locked()
            task.sessdata = sessdata
        return sessdata

    def _download_stream(
        self,
        task_id: str,
        stream: StreamInfo,
        sessdata: str,
        target_path: Path,
        errors: list[str],
        cancel_event: threading.Event,
        stop_event: threading.Event,
    ) -> None:
        candidate_urls = [stream.url, *stream.backup_urls]
        attempt_errors: list[str] = []

        for index, candidate_url in enumerate(candidate_urls, start=1):
            try:
                if cancel_event.is_set() or stop_event.is_set():
                    return
                if index > 1:
                    self.logger(f"任务 {task_id} 的 {stream.kind} 流切换到备用地址 #{index}")

                with self.api.http_client.stream(candidate_url, sessdata=sessdata) as response:
                    with target_path.open("wb") as file_handle:
                        for chunk in response.iter_bytes(chunk_size=1024 * 128):
                            if self._stop_event.is_set():
                                stop_event.set()
                                return
                            if cancel_event.is_set() or stop_event.is_set():
                                return
                            if not chunk:
                                continue
                            self._rate_limiter.acquire(len(chunk))
                            if cancel_event.is_set() or stop_event.is_set():
                                return
                            file_handle.write(chunk)
                            self._update_progress(task_id, stream.kind, len(chunk))
                return
            except Exception as exc:
                if cancel_event.is_set() or stop_event.is_set():
                    return
                attempt_errors.append(f"{candidate_url}: {exc}")

        stop_event.set()
        errors.append(f"{stream.kind} 流下载失败：{' | '.join(attempt_errors)}")

    def _set_status(self, task_id: str, status: TaskStatus, message: str) -> None:
        with self._tasks_lock:
            task = self._tasks[task_id]
            task.status = status
            task.message = message
            if status == TaskStatus.DOWNLOADING and task.started_at is None:
                task.started_at = time.time()
            if status != TaskStatus.DOWNLOADING:
                task.progress.speed_bps = 0.0
            self._save_tasks_locked()

    def _set_totals(self, task_id: str, video_size: int, audio_size: int) -> None:
        with self._tasks_lock:
            task = self._tasks[task_id]
            task.progress.video_total_bytes = video_size
            task.progress.audio_total_bytes = audio_size
            task.progress.total_bytes = video_size + audio_size
            self._save_tasks_locked()

    def _update_progress(self, task_id: str, stream_kind: str, chunk_size: int) -> None:
        now = time.monotonic()
        with self._tasks_lock:
            task = self._tasks[task_id]
            if stream_kind == "video":
                task.progress.video_downloaded_bytes += chunk_size
            else:
                task.progress.audio_downloaded_bytes += chunk_size

            progress = task.progress
            progress.downloaded_bytes = progress.video_downloaded_bytes + progress.audio_downloaded_bytes
            if progress.total_bytes > 0:
                progress.percent = min(100.0, progress.downloaded_bytes * 100 / progress.total_bytes)

            task._speed_samples.append((now, progress.downloaded_bytes))
            while len(task._speed_samples) > 2 and now - task._speed_samples[0][0] > 1.5:
                task._speed_samples.popleft()

            if len(task._speed_samples) >= 2:
                elapsed = task._speed_samples[-1][0] - task._speed_samples[0][0]
                if elapsed > 0:
                    bytes_delta = task._speed_samples[-1][1] - task._speed_samples[0][1]
                    progress.speed_bps = max(0.0, bytes_delta / elapsed)

    def _fail_task(self, task_id: str, message: str) -> None:
        friendly_message = humanize_error(message)
        with self._tasks_lock:
            task = self._tasks[task_id]
            task.status = TaskStatus.FAILED
            task.error = friendly_message
            task.message = friendly_message
            task.finished_at = time.time()
            task.progress.speed_bps = 0.0
            task._speed_samples.clear()
            title = task.title
            self._save_tasks_locked()
        self.logger(f"任务失败：{title} | {message}")

    def _cancel_active_task(self, task_id: str, message: str = "已取消") -> None:
        with self._tasks_lock:
            task = self._tasks[task_id]
            self._mark_canceled_locked(task, message)
            title = task.title
            self._save_tasks_locked()
        self.logger(f"任务已取消：{title}")

    def _mark_canceled_locked(self, task: DownloadTask, message: str = "已取消") -> None:
        task.status = TaskStatus.CANCELED
        task.error = ""
        task.message = message
        task.finished_at = time.time()
        task.progress.speed_bps = 0.0
        task._speed_samples.clear()

    def _ensure_not_cancelled(self, cancel_event: threading.Event) -> None:
        if self._stop_event.is_set():
            raise DownloadCancelled("程序退出")
        if cancel_event.is_set():
            raise DownloadCancelled("已取消")

    def _task_sort_key(self, task: DownloadTask, queue_positions: dict[str, int]) -> tuple[float, float]:
        if task.status == TaskStatus.DOWNLOADING:
            return (0, task.started_at or task.created_at)
        if task.status == TaskStatus.PREPARING:
            return (1, task.created_at)
        if task.status == TaskStatus.MERGING:
            return (2, task.started_at or task.created_at)
        if task.status == TaskStatus.QUEUED:
            return (3, queue_positions.get(task.task_id, 10**9))
        if task.status == TaskStatus.FAILED:
            return (4, -(task.finished_at or task.created_at))
        if task.status == TaskStatus.CANCELED:
            return (5, -(task.finished_at or task.created_at))
        return (6, -(task.finished_at or task.created_at))

    def _serialize_task(self, task: DownloadTask, queue_position: int | None) -> dict:
        can_cancel = task.status not in TERMINAL_STATUSES and not task.cancel_event.is_set()
        can_retry = task.status in RETRYABLE_STATUSES
        can_remove = task.status in TERMINAL_STATUSES or task.status == TaskStatus.QUEUED
        return {
            "task_id": task.task_id,
            "title": task.title,
            "status": task.status.value,
            "message": task.message,
            "error": task.error,
            "quality_label": task.quality.label,
            "output_path": task.output_path,
            "save_dir": task.save_dir,
            "source_url": task.source_url,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "percent": task.progress.percent,
            "total_bytes": task.progress.total_bytes,
            "downloaded_bytes": task.progress.downloaded_bytes,
            "speed_bps": task.progress.speed_bps,
            "video_total_bytes": task.progress.video_total_bytes,
            "video_downloaded_bytes": task.progress.video_downloaded_bytes,
            "audio_total_bytes": task.progress.audio_total_bytes,
            "audio_downloaded_bytes": task.progress.audio_downloaded_bytes,
            "queue_position": queue_position,
            "can_cancel": can_cancel,
            "can_retry": can_retry,
            "can_remove": can_remove,
            "cancel_requested": task.cancel_event.is_set() and task.status not in TERMINAL_STATUSES,
        }
