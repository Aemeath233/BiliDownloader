from __future__ import annotations

import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bili_client.core.errors import humanize_error
from bili_client.core.models import (
    AppConfig,
    DownloadTask,
    EpisodeInfo,
    ProgressSnapshot,
    QualityOption,
    StreamInfo,
    TaskStatus,
    safe_filename,
)
from bili_client.services.bili_api import BiliApiError, BilibiliApi, QualityUnavailableError
from bili_client.services.ffmpeg import FFmpegService
from bili_client.services.rate_limiter import GlobalRateLimiter
from bili_client.services.task_store import TaskStore

TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}
RETRYABLE_STATUSES = {TaskStatus.FAILED, TaskStatus.CANCELED}


class DownloadCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DownloadTaskSpec:
    source_url: str
    episode: EpisodeInfo
    quality: QualityOption
    save_dir: str
    sessdata: str = ""
    filename_stem: str | None = None


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

        self._steady_mode = bool(config.steady_mode)
        self._max_concurrent_tasks = self._effective_max_concurrent_tasks(config)
        self._cleanup_temp_files = bool(config.cleanup_temp_files)
        self._rate_limiter = GlobalRateLimiter(int(max(0.0, config.speed_limit_mb) * 1024 * 1024))
        self.ffmpeg.set_command(config.ffmpeg_path)

        self._load_persisted_tasks()

        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="download-scheduler")
        self._scheduler_thread.start()

    def set_auth_refresher(self, auth_refresher: Callable[[bool], str] | None) -> None:
        self.auth_refresher = auth_refresher

    def update_config(self, config: AppConfig) -> None:
        with self._config_lock:
            self._steady_mode = bool(config.steady_mode)
            self._max_concurrent_tasks = self._effective_max_concurrent_tasks(config)
            self._cleanup_temp_files = bool(config.cleanup_temp_files)
            self._rate_limiter.update_limit(int(max(0.0, config.speed_limit_mb) * 1024 * 1024))
            self.ffmpeg.set_command(config.ffmpeg_path)
            strategy = "稳妥模式" if self._steady_mode else "标准模式"
        with self._tasks_lock:
            for task in self._tasks.values():
                task.sessdata = config.sessdata.strip()
        self.logger(
            f"运行配置已更新：{strategy}，并发任务 {self._max_concurrent_tasks}，"
            f"全局限速 {config.speed_limit_mb or 0:.2f} MB/s"
        )

    def _effective_max_concurrent_tasks(self, config: AppConfig) -> int:
        requested = min(16, max(1, int(config.max_concurrent_tasks)))
        if bool(config.steady_mode):
            return min(requested, 2)
        return requested

    def create_task(
        self,
        source_url: str,
        episode: EpisodeInfo,
        quality: QualityOption,
        save_dir: str,
        sessdata: str,
        filename_stem: str | None = None,
    ) -> str:
        return self.create_tasks(
            [
                DownloadTaskSpec(
                    source_url=source_url,
                    episode=episode,
                    quality=quality,
                    save_dir=save_dir,
                    sessdata=sessdata,
                    filename_stem=filename_stem,
                )
            ]
        )[0]

    def create_tasks(self, specs: list[DownloadTaskSpec]) -> list[str]:
        if not specs:
            return []

        prepared: list[tuple[DownloadTaskSpec, Path, str]] = []
        for spec in specs:
            save_path = Path(spec.save_dir)
            save_path.mkdir(parents=True, exist_ok=True)
            output_stem = safe_filename(spec.filename_stem or spec.episode.title)
            prepared.append((spec, save_path, output_stem))

        created_tasks: list[DownloadTask] = []
        with self._tasks_lock:
            reserved_paths = {
                self._path_key(Path(task.output_path))
                for task in self._tasks.values()
                if task.status not in TERMINAL_STATUSES or Path(task.output_path).exists()
            }
            try:
                for spec, save_path, output_stem in prepared:
                    task_id = uuid.uuid4().hex
                    output_path = self._next_available_path(
                        save_path / f"{output_stem}.mp4",
                        reserved_paths=reserved_paths,
                    )
                    reserved_paths.add(self._path_key(output_path))
                    task = DownloadTask(
                        task_id=task_id,
                        source_url=spec.source_url,
                        output_path=str(output_path),
                        save_dir=str(save_path),
                        sessdata=spec.sessdata.strip(),
                        episode=spec.episode,
                        quality=spec.quality,
                        title=spec.episode.title,
                    )
                    self._tasks[task_id] = task
                    self._queue.append(task_id)
                    created_tasks.append(task)
                self._save_tasks_locked()
            except Exception:
                created_ids = {task.task_id for task in created_tasks}
                self._queue = [task_id for task_id in self._queue if task_id not in created_ids]
                for task_id in created_ids:
                    self._tasks.pop(task_id, None)
                raise

        for task in created_tasks:
            self.logger(f"任务已加入队列：{task.title} [{task.quality.label}]")
        return [task.task_id for task in created_tasks]

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
                DownloadTaskSpec(
                    source_url=task.source_url,
                    episode=task.episode,
                    quality=task.quality,
                    save_dir=task.save_dir,
                    sessdata=task.sessdata,
                    filename_stem=Path(task.output_path).stem,
                )
                for task in self._tasks.values()
                if task.status in retry_statuses
            ]

        created_ids = self.create_tasks(specs)

        if created_ids:
            self.logger(f"已重新加入 {len(created_ids)} 个失败/取消任务")
        return len(created_ids)

    def retry_task(self, task_id: str) -> str | None:
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if task is None or task.status not in RETRYABLE_STATUSES:
                return None
            spec = (
                task.source_url,
                task.episode,
                task.quality,
                task.save_dir,
                task.sessdata,
                task.title,
                Path(task.output_path).stem,
            )

        source_url, episode, quality, save_dir, sessdata, title, filename_stem = spec
        new_task_id = self.create_task(
            source_url=source_url,
            episode=episode,
            quality=quality,
            save_dir=save_dir,
            sessdata=sessdata,
            filename_stem=filename_stem,
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
            metrics = self._metrics_locked()

        return {
            "tasks": tasks,
            "metrics": metrics,
        }

    def metrics(self) -> dict:
        with self._tasks_lock:
            return self._metrics_locked()

    def _metrics_locked(self) -> dict:
        counts = {status: 0 for status in TaskStatus}
        total_speed = 0.0
        for task in self._tasks.values():
            counts[task.status] += 1
            if task.status == TaskStatus.DOWNLOADING:
                total_speed += task.progress.speed_bps
        return {
            "active_count": len(self._active_ids),
            "queued_count": counts[TaskStatus.QUEUED],
            "completed_count": counts[TaskStatus.COMPLETED],
            "failed_count": counts[TaskStatus.FAILED],
            "canceled_count": counts[TaskStatus.CANCELED],
            "total_speed_bps": total_speed,
            "speed_limit_bps": self._rate_limiter.get_limit(),
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
                if "sessdata" in payload:
                    dirty = True
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
            task_id = str(payload.get("task_id") or uuid.uuid4().hex[:10])
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", task_id):
                return None
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
                task_id=task_id,
                source_url=str(payload.get("source_url") or ""),
                output_path=output_path,
                save_dir=save_dir,
                sessdata="",
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
        except (TypeError, ValueError, OverflowError):
            return None

    def _next_available_path(self, candidate: Path, reserved_paths: set[str] | None = None) -> Path:
        reserved = reserved_paths or set()
        if not candidate.exists() and self._path_key(candidate) not in reserved:
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix
        parent = candidate.parent
        counter = 1
        while True:
            next_path = parent / f"{stem} ({counter}){suffix}"
            if not next_path.exists() and self._path_key(next_path) not in reserved:
                return next_path
            counter += 1

    @staticmethod
    def _path_key(path: Path) -> str:
        return os.path.normcase(os.path.abspath(str(path)))

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
                worker = threading.Thread(
                    target=self._run_task, args=(task_id,), daemon=True, name=f"download-{task_id}"
                )
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
        video_path = temp_dir / "video.m4s"
        audio_path = temp_dir / "audio.m4s"

        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
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
            with self._config_lock:
                cleanup_temp_files = self._cleanup_temp_files
            if cleanup_temp_files:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _get_streams_for_task(
        self,
        task_id: str,
        task: DownloadTask,
    ) -> tuple[StreamInfo, StreamInfo | None, str]:
        sessdata = self._refresh_task_sessdata(task_id, task, force=False)
        try:
            video_stream, audio_stream = self.api.get_streams(task.episode, task.quality.qn, sessdata)
            self._apply_actual_quality(task_id, task, video_stream)
            return video_stream, audio_stream, sessdata
        except BiliApiError:
            if task.quality.qn < 80 or self.auth_refresher is None:
                raise

            refreshed_sessdata = self._refresh_task_sessdata(task_id, task, force=True)
            if not refreshed_sessdata or refreshed_sessdata == sessdata:
                raise

            self.logger(f"高画质取流失败，已刷新登录态后重试一次：{task.title}")
            video_stream, audio_stream = self.api.get_streams(task.episode, task.quality.qn, refreshed_sessdata)
            self._apply_actual_quality(task_id, task, video_stream)
            return video_stream, audio_stream, refreshed_sessdata

    def _apply_actual_quality(self, task_id: str, task: DownloadTask, video_stream: StreamInfo) -> None:
        actual_qn = int(video_stream.quality_qn or 0)
        if actual_qn <= 0 or actual_qn == task.quality.qn:
            return
        actual_label = video_stream.quality_label or f"QN {actual_qn}"
        requested_label = task.quality.label
        raise QualityUnavailableError(
            f"所选画质“{requested_label}”当前不可下载，接口实际返回“{actual_label}”。"
            "任务已停止，未自动降档；请重新选择当前可下载的画质。"
        )

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
        headers = {"Referer": stream.referer} if stream.referer else None

        for index, candidate_url in enumerate(candidate_urls, start=1):
            try:
                if cancel_event.is_set() or stop_event.is_set():
                    return
                if index > 1:
                    self.logger(f"任务 {task_id} 的 {stream.kind} 流切换到备用地址 #{index}")

                with self.api.http_client.stream(candidate_url, sessdata=sessdata, extra_headers=headers) as response:
                    self._reset_stream_progress(task_id, stream.kind)
                    written = 0
                    with target_path.open("wb") as file_handle:
                        for chunk in response.iter_bytes(chunk_size=1024 * 128):
                            if self._stop_event.is_set():
                                stop_event.set()
                                return
                            if cancel_event.is_set() or stop_event.is_set():
                                return
                            if not chunk:
                                continue
                            acquired = self._rate_limiter.acquire(
                                len(chunk),
                                cancel_events=(cancel_event, stop_event, self._stop_event),
                            )
                            if not acquired:
                                return
                            if cancel_event.is_set() or stop_event.is_set():
                                return
                            file_handle.write(chunk)
                            written += len(chunk)
                            self._update_progress(task_id, stream.kind, len(chunk))
                    if written == 0 or (stream.size_bytes > 0 and written != stream.size_bytes):
                        raise RuntimeError("媒体流不完整，将尝试备用地址或稍后重试")
                return
            except Exception as exc:
                if cancel_event.is_set() or stop_event.is_set():
                    return
                safe_error = re.sub(r"https?://\S+", "<媒体地址>", str(exc))[:400]
                attempt_errors.append(f"地址 #{index}: {safe_error or type(exc).__name__}")

        stop_event.set()
        errors.append(f"{stream.kind} 流下载失败：{' | '.join(attempt_errors)}")

    def _reset_stream_progress(self, task_id: str, stream_kind: str) -> None:
        with self._tasks_lock:
            task = self._tasks[task_id]
            progress = task.progress
            if stream_kind == "video":
                progress.video_downloaded_bytes = 0
            else:
                progress.audio_downloaded_bytes = 0
            progress.downloaded_bytes = progress.video_downloaded_bytes + progress.audio_downloaded_bytes
            progress.percent = progress.downloaded_bytes * 100 / progress.total_bytes if progress.total_bytes else 0.0
            progress.speed_bps = 0.0
            task._speed_samples.clear()

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
