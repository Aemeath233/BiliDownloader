from __future__ import annotations

import base64
import io
import math
import os
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path

from bili_client.core.errors import humanize_error
from bili_client.core.models import DownloadMode, MediaParseResult, safe_filename
from bili_client.core.naming import render_filename_template
from bili_client.core.security import redact_sensitive
from bili_client.services.download_manager import DownloadTaskSpec


class DesktopBridge:
    """Expose one command gateway, never service objects, cookies or shell commands."""

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._window = None
        self._media = {}
        self._qualities_cache = {}
        self._lock = threading.RLock()
        self._quality_lock = threading.Lock()
        self._qr = None
        self._qr_version = 0
        self._frontend_ready = False

    def invoke(self, command: str, payload=None) -> dict:
        try:
            commands = {
                "state": self._state,
                "ui_ready": self._ui_ready,
                "parse": self._parse,
                "qualities": self._qualities,
                "add": self._add,
                "preview": self._preview,
                "task_action": self._task_action,
                "reselect": self._reselect,
                "save_settings": self._save_settings,
                "pick_directory": self._pick_directory,
                "choose_destination": self._choose_destination,
                "pick_ffmpeg": self._pick_ffmpeg,
                "open_location": self._open_location,
                "qr_start": self._qr_start,
                "qr_poll": self._qr_poll,
                "qr_cancel": self._qr_cancel,
                "account": self._account,
                "check_account": self._check_account,
                "close": self._close,
            }
            if command not in commands:
                raise ValueError("不支持的操作")
            if payload is not None and not isinstance(payload, dict):
                raise ValueError("参数格式无效")
            if command != "state" and not self._runtime.ready.is_set():
                raise ValueError("下载服务尚未就绪，请稍候")
            if self._runtime.closed.is_set():
                raise ValueError("程序正在退出")
            return {"ok": True, "data": commands[command](payload or {})}
        except Exception as exc:
            self._runtime.emit(f"操作 {command} 失败：{exc}")
            return {"ok": False, "error": humanize_error(redact_sensitive(str(exc)))}

    def _ui_ready(self, _data):
        with self._lock:
            if not self._frontend_ready:
                self._runtime.emit("内嵌界面就绪：WebView2 与 Python 通信正常")
                self._frontend_ready = True
        return True

    def _state(self, data) -> dict:
        rt = self._runtime
        with rt.lock:
            result = dict(
                ready=rt.ready.is_set(),
                startup_error=rt.startup_error,
                config=rt.public_config(),
                logged_in=bool(rt.config.sessdata),
                auth_version=rt.auth_version,
            )
            cursor = int(data.get("log_cursor", 0))
            result["logs"] = [item for item in rt.logs if item["id"] > cursor][:500]
            result["log_cursor"] = result["logs"][-1]["id"] if result["logs"] else rt.log_sequence
        if result["ready"]:
            snapshot = rt.manager.snapshot() if data.get("include_tasks", True) else {"metrics": rt.manager.metrics()}
            result.update(snapshot)
            result["persistence_error"] = rt.manager.persistence_error
            result["ffmpeg_available"] = rt.ffmpeg.is_available()
        return result

    def _remember(self, media: MediaParseResult, save_dir=None) -> dict:
        key = uuid.uuid4().hex
        with self._lock:
            self._media[key] = (media, save_dir)
            while len(self._media) > 8:
                del self._media[next(iter(self._media))]
        return {"id": key, **asdict(media)}

    def _parse(self, data) -> dict:
        text = data.get("text", "")
        if not isinstance(text, str) or not text.strip() or len(text) > 16384:
            raise ValueError("请输入有效的 B 站链接或编号")
        media = self._runtime.api.parse_media(text, sessdata=self._runtime.refresh_auth())
        self._runtime.emit(f"解析完成：{media.title}，共 {len(media.episodes)} 个条目")
        return self._remember(media)

    def _selection(self, data):
        with self._lock:
            entry = self._media.get(data.get("media_id"))
        if entry is None:
            raise ValueError("解析结果已过期，请重新解析链接")
        media, save_dir = entry
        indices = data.get("indices", [data.get("index", media.default_episode_index)])
        if not isinstance(indices, list) or not indices or len(indices) > len(media.episodes):
            raise ValueError("请至少选择一个分集")
        if any(type(index) is not int or not 0 <= index < len(media.episodes) for index in indices):
            raise ValueError("分集选择无效")
        return media, save_dir, sorted(set(indices))

    @staticmethod
    def _download_mode(data):
        try:
            return DownloadMode(data.get("download_mode", "combined"))
        except (ValueError, TypeError):
            raise ValueError("下载内容无效，请选择视频＋音频、仅音频或仅视频") from None

    def _quality_options(self, media_id, media, index, refresh=False, download_mode=DownloadMode.COMBINED):
        rt = self._runtime
        with self._quality_lock:
            with rt.lock:
                sessdata, version = rt.config.sessdata, rt.auth_version
            key = (media_id, index, version, download_mode)
            cached = self._qualities_cache.get(key)
            if not refresh and cached and time.monotonic() - cached[0] < 45:
                return cached[1], version
            if refresh:
                rt.api.clear_stream_cache(media.episodes[index], sessdata)
            fetch = (
                rt.api.get_audio_quality_options if download_mode == DownloadMode.AUDIO else rt.api.get_quality_options
            )
            options = fetch(media.episodes[index], sessdata=sessdata)
            if version != rt.auth_version:
                raise ValueError("账号已变化，请重新读取画质")
            self._qualities_cache[key] = (time.monotonic(), options)
            if len(self._qualities_cache) > 128:
                del self._qualities_cache[next(iter(self._qualities_cache))]
            return options, version

    def _qualities(self, data) -> dict:
        media, _, indices = self._selection(data)
        options, version = self._quality_options(
            data["media_id"], media, indices[0], bool(data.get("refresh")), self._download_mode(data)
        )
        return {"options": [asdict(option) for option in options], "auth_version": version}

    def _specs(self, data):
        rt = self._runtime
        media, override_dir, indices = self._selection(data)
        download_mode = self._download_mode(data)
        quality_index = data.get("quality_index", indices[0])
        if type(quality_index) is not int or not 0 <= quality_index < len(media.episodes):
            raise ValueError("画质对应的分集无效")
        options, version = self._quality_options(data["media_id"], media, quality_index, download_mode=download_mode)
        with rt.lock:
            if version != rt.auth_version:
                raise ValueError("账号已变化，请重新读取画质")
            config = replace(rt.config)
        quality = next((option for option in options if type(data.get("qn")) is int and option.qn == data["qn"]), None)
        if quality is None:
            raise ValueError("所选画质不可用，请刷新画质后重试")
        directory = Path(override_dir or config.save_path)
        if not override_dir and config.organize_by_source:
            directory /= safe_filename(media.folder_title or media.source_name or media.title)
        specs = [
            DownloadTaskSpec(
                media.normalized_url,
                media.episodes[index],
                quality,
                str(directory),
                config.sessdata,
                render_filename_template(
                    config.filename_template,
                    media_title=media.title,
                    episode=media.episodes[index],
                    quality=quality,
                    index=position + 1,
                    total=len(indices),
                ),
                download_mode,
            )
            for position, index in enumerate(indices)
        ]
        return specs, version

    def _preview(self, data):
        specs, _ = self._specs(data)
        return {
            "directory": specs[0].save_dir,
            "filename": specs[0].filename_stem + specs[0].download_mode.extension,
            "count": len(specs),
        }

    def _add(self, data):
        rt = self._runtime
        if not rt.ffmpeg.is_available():
            raise ValueError("未找到 FFmpeg，请先在设置中选择 ffmpeg.exe")
        specs, version = self._specs(data)
        with self._lock, rt.lock:
            if rt.closed.is_set():
                raise ValueError("程序正在退出")
            if version != rt.auth_version:
                raise ValueError("账号已变化，请刷新画质")
            duplicates = rt.manager.count_duplicates(
                [spec.episode for spec in specs], specs[0].quality.qn, specs[0].download_mode
            )
            if duplicates and data.get("allow_duplicates") is not True:
                return {"duplicates": duplicates, "created": 0}
            ids = rt.manager.create_tasks(specs)
        return {"created": len(ids), "duplicates": 0}

    def _task_action(self, data):
        manager = self._runtime.manager
        action = data.get("action")
        if action == "retry_all":
            return {"count": manager.retry_terminal_tasks()}
        if action == "clear_finished":
            return {"count": manager.clear_finished()}
        methods = {"cancel": manager.cancel_task, "retry": manager.retry_task, "remove": manager.remove_task}
        ids = data.get("ids")
        if action not in methods or not isinstance(ids, list) or not ids or len(ids) > 10000:
            raise ValueError("任务操作无效")
        if any(not isinstance(key, str) for key in ids):
            raise ValueError("任务编号无效")
        return {"count": sum(bool(methods[action](key)) for key in set(ids))}

    def _reselect(self, data):
        spec = self._runtime.manager.get_task_spec(data.get("task_id"))
        if spec is None:
            raise ValueError("当前任务无法重新选择画质")
        media = MediaParseResult(spec.source_url, spec.source_url, spec.episode.title, [spec.episode])
        return {
            **self._remember(media, spec.save_dir),
            "preferred_qn": spec.quality.qn,
            "download_mode": spec.download_mode.value,
        }

    def _save_settings(self, data):
        rt = self._runtime
        allowed = set(rt.public_config())
        if set(data) - allowed:
            raise ValueError("设置中包含未知字段")
        values = rt.public_config() | data
        try:
            values["default_download_mode"] = DownloadMode(values["default_download_mode"]).value
        except (ValueError, TypeError):
            raise ValueError("默认下载内容无效") from None
        for key in ("organize_by_source", "cleanup_temp_files", "steady_mode", "animations_enabled"):
            if type(values[key]) is not bool:
                raise ValueError("开关值无效")
        if type(values["max_concurrent_tasks"]) is not int or not 1 <= values["max_concurrent_tasks"] <= 16:
            raise ValueError("并发任务必须是 1 到 16 之间的整数")
        speed = values["speed_limit_mb"]
        if type(speed) not in (int, float) or not math.isfinite(speed) or speed < 0:
            raise ValueError("限速必须是大于等于 0 的数字")
        if values["appearance_mode"] not in ("system", "light", "dark"):
            raise ValueError("主题无效")
        for key in ("save_path", "ffmpeg_path", "filename_template"):
            if not isinstance(values[key], str) or not values[key].strip() or len(values[key]) > 2048:
                raise ValueError("保存位置、FFmpeg 路径和文件名模板不能为空或过长")
            values[key] = values[key].strip()
        Path(values["save_path"]).mkdir(parents=True, exist_ok=True)
        with rt.lock:
            if rt.closed.is_set():
                raise ValueError("程序正在退出")
            config = replace(rt.config, **values)
            rt.config_store.save(config)
            rt.config = config
            rt.manager.update_config(config)
            rt.http.set_steady_mode(config.steady_mode)
            rt.ffmpeg.set_command(config.ffmpeg_path)
        return rt.public_config()

    def _choose_destination(self, data):
        key = data.get("media_id")
        if not isinstance(key, str):
            raise ValueError("请先解析视频链接")
        with self._lock:
            if key not in self._media:
                raise ValueError("解析结果已过期，请重新解析链接")
        # The path comes from the native folder picker, never a frontend-supplied command.
        selected = self._pick_directory({})
        if not selected:
            return None
        with self._lock:
            if self._runtime.closed.is_set():
                raise ValueError("程序正在退出")
            entry = self._media.get(key)
            if entry is None:
                raise ValueError("解析结果已过期，请重新解析链接")
            self._media[key] = (entry[0], str(selected))
        return str(selected)

    def _pick_directory(self, _data):
        import webview

        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return result[0] if result else None

    def _pick_ffmpeg(self, _data):
        import webview

        result = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=("FFmpeg (ffmpeg.exe)",))
        return result[0] if result else None

    def _open_location(self, data):
        rt = self._runtime
        kind = data.get("kind", "save")
        if kind in ("save", "logs"):
            path = Path(rt.config.save_path) if kind == "save" else rt.config_store.logs_dir
        else:
            task = next(
                (task for task in rt.manager.snapshot()["tasks"] if task["task_id"] == data.get("task_id")), None
            )
            if task is None or kind not in ("reveal", "play", "directory"):
                raise ValueError("文件操作无效")
            if kind == "play" and task["status"] != "completed":
                raise ValueError("任务尚未完成")
            path = Path(task["save_dir"] if kind == "directory" else task["output_path"])
        if not path.exists():
            raise ValueError("文件或目录不存在，可能已被移动")
        if kind == "play" and path.suffix.lower() not in (".mp4", ".m4a"):
            raise ValueError("只能打开已下载的 MP4 视频或 M4A 音频")
        if kind == "reveal":
            subprocess.Popen(
                [str(Path(os.environ.get("WINDIR", "C:/Windows")) / "explorer.exe"), "/select,", str(path.resolve())]
            )
        else:
            os.startfile(str(path.resolve()))
        return True

    def _qr_start(self, _data):
        import qrcode

        with self._lock:
            self._qr_version += 1
            version = self._qr_version
        info = self._runtime.auth.create_qr_login()
        output = io.BytesIO()
        qrcode.make(info.url).save(output, format="PNG")
        with self._lock:
            if version != self._qr_version or self._runtime.closed.is_set():
                raise ValueError("扫码登录已取消")
            self._qr = (version, info.qrcode_key, time.monotonic() + 180)
        return {"image": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode(), "version": version}

    def _qr_poll(self, data):
        with self._lock:
            qr = self._qr
        if not qr or data.get("version") != qr[0] or time.monotonic() > qr[2]:
            return {"code": 86038, "message": "二维码已过期，请刷新"}
        result = self._runtime.auth.poll_qr_login(qr[1], save=False)
        with self._lock, self._runtime.lock:
            if self._qr is not qr or self._runtime.closed.is_set():
                return {"code": 86038, "message": "扫码登录已取消"}
            if result.code == 0 and result.session is not None:
                self._runtime.auth.save_session(result.session)
                self._runtime.accept_account(result.session.sessdata)
                self._qr = None
        return {"code": result.code, "message": result.message}

    def _qr_cancel(self, _data):
        with self._lock:
            self._qr_version += 1
            self._qr = None
        return True

    def _account(self, data):
        token = data.get("sessdata", "")
        if not isinstance(token, str) or len(token) > 4096:
            raise ValueError("登录凭证无效")
        with self._lock, self._runtime.lock:
            if self._runtime.closed.is_set():
                raise ValueError("程序正在退出")
            self._qr_version += 1
            self._qr = None
            self._runtime.auth.set_sessdata(token)
            self._runtime.accept_account(token.strip())
        return {"logged_in": bool(token.strip())}

    def _check_account(self, _data):
        rt = self._runtime
        session = rt.auth.ensure_latest_session(rt.config.sessdata, force=True)
        with rt.lock:
            if session is not None and rt.auth.load_session() is session:
                rt.accept_account(session.sessdata)
        return {"logged_in": bool(rt.config.sessdata)}

    def _close(self, data):
        if data.get("mode") == "minimize":
            self._window.minimize()
        elif data.get("mode") == "exit":
            self._window._allow_close = True
            self._window.destroy()
        return True
