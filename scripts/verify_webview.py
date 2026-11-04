"""Exercise the actual embedded renderer against isolated deterministic services."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bili_client.core.models import AppConfig, EpisodeInfo, MediaParseResult, QualityOption  # noqa: E402
from bili_client.desktop.runtime import DesktopRuntime  # noqa: E402
from bili_client.desktop.window import run_desktop  # noqa: E402
from tests.test_ui_integration import task_snapshot  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--compact", action="store_true", help="Check the minimum supported window size")
    parser.add_argument("--screenshots", action="store_true", help="Export renderer previews for visual QA")
    parser.add_argument(
        "--native-close-check", action="store_true", help="Check the native closing event with active tasks"
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="bili-webview-check-") as temporary:
        runtime = DesktopRuntime(Mock())
        runtime.config = AppConfig(save_path=temporary, appearance_mode="light")
        runtime.manager, runtime.api, runtime.auth, runtime.ffmpeg, runtime.http = (Mock() for _ in range(5))
        runtime.config_store.logs_dir = Path(temporary)
        tasks = [task_snapshot(index, "downloading" if index == 0 else "completed") for index in range(75)]
        tasks[0]["title"] = "设计与生活：一段旅程的开始"
        runtime.manager.snapshot.side_effect = lambda: {
            "tasks": tasks,
            "metrics": dict(
                active_count=1,
                queued_count=0,
                completed_count=len(tasks) - 1,
                failed_count=0,
                canceled_count=0,
                total_speed_bps=3240000,
            ),
        }
        runtime.manager.metrics.side_effect = lambda: runtime.manager.snapshot()["metrics"]
        runtime.manager.persistence_error = ""
        runtime.manager.has_unfinished_tasks.return_value = False
        runtime.manager.count_duplicates.return_value = 0

        def create(specs):
            for spec in specs:
                item = task_snapshot(len(tasks), "queued")
                item["title"] = spec.episode.title
                item["can_cancel"] = True
                item["download_mode"] = spec.download_mode.value
                item["download_mode_label"] = spec.download_mode.label
                item["quality_label"] = spec.quality.label
                item["output_path"] = str(Path(spec.save_dir) / (spec.filename_stem + spec.download_mode.extension))
                tasks.insert(0, item)
            return [str(index) for index in range(len(specs))]

        runtime.manager.create_tasks.side_effect = create
        sample_media = MediaParseResult(
            "source",
            "https://www.bilibili.com/video/BV1xx411c7mD",
            "自然纪录片 · 山川之间",
            [
                EpisodeInfo(f"第 {index + 1} 集：沿着山川去旅行", "BV1xx411c7mD", index + 1, False)
                for index in range(25)
            ],
        )
        runtime.api.parse_media.side_effect = lambda text, **_kwargs: (
            replace(sample_media, title="夏日微风 · 单个视频", episodes=sample_media.episodes[:1])
            if text == "single-video"
            else sample_media
        )
        runtime.api.get_quality_options.return_value = [QualityOption(80, "1080P"), QualityOption(64, "720P")]
        runtime.api.get_audio_quality_options.return_value = [
            QualityOption(30280, "音频 192 kbps", "保存为 M4A，保留源编码，不转码。")
        ]
        runtime.auth.ensure_latest_session.return_value = None
        runtime.ffmpeg.is_available.return_value = True

        def initialize():
            runtime.ready.set()
            runtime.emit("演示服务就绪，无真实账号或网络下载")

        runtime.initialize = initialize
        result = {}

        def loaded(window, _runtime):
            if args.compact:
                window.resize(760, 560)
            if args.preview:
                return
            try:
                if args.screenshots:
                    window.evaluate_js("window.__captureEnabled = true")
                if args.native_close_check:
                    window.evaluate_js("window.__nativeCloseCheck = true")
                window.evaluate_js((ROOT / "scripts" / "webview_smoke.js").read_text(encoding="utf-8"))
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    phase = window.evaluate_js("window.__capturePhase || null")
                    if phase == "native-close" and args.native_close_check:
                        runtime.manager.has_unfinished_tasks.return_value = True
                        window.destroy()
                        window.evaluate_js("window.__capturePhase = null")
                    elif phase and args.screenshots:
                        result.setdefault("layouts", {})[phase] = window.evaluate_js(
                            "({bodyClass:document.body.className, labelSize:getComputedStyle(document.querySelector('.link-form>label')).fontSize, mode:document.querySelector('[data-download-mode][aria-pressed=true]').dataset.downloadMode, width:innerWidth})"
                        )
                        from System import Func, Object

                        task = window.native.Invoke(
                            Func[Object](
                                lambda: window.native.webview.CoreWebView2.CallDevToolsProtocolMethodAsync(
                                    "Page.captureScreenshot", '{"format":"png","captureBeyondViewport":false}'
                                )
                            )
                        )
                        if not task.Wait(5000):
                            raise TimeoutError("Renderer screenshot did not finish within 5 seconds")
                        capture = json.loads(str(task.Result))
                        prefix = "compact-" if args.compact else ""
                        output_image = ROOT / "build" / f"webview-{prefix}{phase}.png"
                        output_image.write_bytes(base64.b64decode(capture["data"]))
                        window.evaluate_js("window.__capturePhase = null")
                    value = window.evaluate_js("window.__smokeResult || null")
                    if value:
                        result.update(value)
                        break
                    time.sleep(0.1)
                else:
                    result.update(ok=False, error="Embedded UI check timed out")
            except Exception as exc:
                result.update(ok=False, error=str(exc))
            finally:
                window._allow_close = True
                window.destroy()

        try:
            with patch(
                "bili_client.desktop.bridge.DesktopBridge._pick_directory", return_value=str(Path(temporary) / "Chosen")
            ):
                run_desktop(runtime, on_loaded=loaded)
        finally:
            runtime.close()
        if not args.preview:
            output = ROOT / "build" / "webview-verification.json"
            output.parent.mkdir(exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=True, indent=2))
            if not result.get("ok"):
                raise SystemExit(1)


if __name__ == "__main__":
    main()
