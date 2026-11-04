from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from bili_client.core.models import AppConfig, EpisodeInfo, MediaParseResult, QualityOption
from bili_client.desktop.bridge import DesktopBridge
from bili_client.desktop.runtime import DesktopRuntime
from bili_client.desktop.window import asset_html, fixed_runtime_path
from bili_client.services.auth_service import AuthSession, QrLoginInfo, QrPollResult


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.rt = DesktopRuntime(Mock())
        self.rt.config = AppConfig(save_path=self.temp.name, sessdata="private-session-token")
        self.rt.manager, self.rt.api, self.rt.auth, self.rt.ffmpeg, self.rt.http = (Mock() for _ in range(5))
        self.rt.manager.snapshot.return_value = {"tasks": [], "metrics": {}}
        self.rt.manager.persistence_error = ""
        self.rt.manager.count_duplicates.return_value = 0
        self.rt.manager.create_tasks.return_value = ["one", "two"]
        self.rt.ffmpeg.is_available.return_value = True
        self.rt.api.get_quality_options.return_value = [QualityOption(80, "1080P")]
        self.rt.ready.set()
        self.rt.refresh_auth = lambda force=False: self.rt.config.sessdata
        self.bridge = DesktopBridge(self.rt)
        self.media = MediaParseResult(
            "source",
            "https://www.bilibili.com/video/BV1xx411c7mD",
            "合集",
            [EpisodeInfo("第一集", "BV1xx411c7mD", 1, False), EpisodeInfo("第二集", "BV1xx411c7mD", 2, False)],
            folder_title="../../outside",
        )
        self.rt.api.parse_media.return_value = self.media

    def invoke(self, command, data=None):
        result = self.bridge.invoke(command, data)
        self.assertTrue(result["ok"], result)
        return result["data"]

    def selection(self):
        media = self.invoke("parse", {"text": "BV1xx411c7mD"})
        return {"media_id": media["id"], "indices": [0, 1], "qn": 80}

    def test_state_never_exposes_cookies_and_logs_are_delivered_in_order(self):
        for index in range(650):
            self.rt.emit(f"entry {index}")
        first = self.invoke("state")
        self.assertEqual(500, len(first["logs"]))
        second = self.invoke("state", {"log_cursor": first["log_cursor"]})
        self.assertEqual(150, len(second["logs"]))
        self.assertNotIn("private-session-token", json.dumps(first))
        self.assertNotIn("sessdata", first["config"])

    def test_parse_quality_preview_and_batch_preserve_filename_contract(self):
        payload = self.selection()
        self.rt.config = replace(self.rt.config, filename_template="{index}_{title}_{quality}", organize_by_source=True)
        preview = self.invoke("preview", payload)
        self.assertEqual("1_第一集_1080P.mp4", preview["filename"])
        self.assertTrue(Path(preview["directory"]).resolve().is_relative_to(Path(self.temp.name)))
        self.assertEqual(2, self.invoke("add", payload)["created"])
        specs = self.rt.manager.create_tasks.call_args.args[0]
        self.assertEqual(2, len(specs))
        self.assertEqual("2_第二集_1080P", specs[1].filename_stem)
        self.assertEqual("private-session-token", specs[0].sessdata)

    def test_duplicate_requires_explicit_second_request(self):
        self.rt.manager.count_duplicates.return_value = 2
        payload = self.selection()
        self.assertEqual(2, self.invoke("add", payload)["duplicates"])
        self.rt.manager.create_tasks.assert_not_called()
        self.assertEqual(2, self.invoke("add", payload | {"allow_duplicates": True})["created"])

    def test_destination_picker_applies_to_current_media_and_tasks_without_changing_default(self):
        payload = self.selection()
        selected = str(Path(self.temp.name) / "Chosen")
        with patch.object(self.bridge, "_pick_directory", return_value=selected):
            self.assertEqual(selected, self.invoke("choose_destination", {"media_id": payload["media_id"]}))
        self.assertEqual(selected, self.invoke("preview", payload)["directory"])
        self.invoke("add", payload)
        self.assertTrue(all(spec.save_dir == selected for spec in self.rt.manager.create_tasks.call_args.args[0]))
        self.assertEqual(self.temp.name, self.rt.config.save_path)
        self.rt.config_store.save.assert_not_called()
        self.assertEqual(self.temp.name, self.invoke("preview", self.selection())["directory"])

    def test_cancelled_destination_picker_keeps_existing_selection(self):
        payload = self.selection()
        original = self.invoke("preview", payload)["directory"]
        with patch.object(self.bridge, "_pick_directory", return_value=None):
            self.assertIsNone(self.invoke("choose_destination", {"media_id": payload["media_id"]}))
        self.assertEqual(original, self.invoke("preview", payload)["directory"])

    def test_destination_picker_rejects_missing_or_evicted_media(self):
        with patch.object(self.bridge, "_pick_directory") as pick:
            self.assertFalse(self.bridge.invoke("choose_destination", {"media_id": "expired"})["ok"])
            self.assertFalse(self.bridge.invoke("choose_destination", {"media_id": []})["ok"])
            pick.assert_not_called()
        payload = self.selection()

        def expire(_data):
            self.bridge._media.clear()
            return self.temp.name

        with patch.object(self.bridge, "_pick_directory", side_effect=expire):
            self.assertFalse(self.bridge.invoke("choose_destination", {"media_id": payload["media_id"]})["ok"])

    def test_download_mode_changes_quality_cache_extension_and_duplicate_scope(self):
        payload = self.selection()
        self.rt.api.get_audio_quality_options.return_value = [QualityOption(30280, "高品质音频")]
        self.invoke("qualities", payload)
        audio = payload | {"download_mode": "audio", "qn": 30280}
        self.assertEqual(30280, self.invoke("qualities", audio)["options"][0]["qn"])
        self.assertTrue(self.invoke("preview", audio)["filename"].endswith(".m4a"))
        self.invoke("add", audio)
        self.assertEqual("audio", self.rt.manager.create_tasks.call_args.args[0][0].download_mode)
        self.assertEqual("audio", self.rt.manager.count_duplicates.call_args.args[2])
        self.assertFalse(self.bridge.invoke("add", payload | {"download_mode": "invalid"})["ok"])
        self.assertFalse(self.bridge.invoke("save_settings", {"default_download_mode": "invalid"})["ok"])

    def test_untrusted_media_indices_quality_and_commands_are_rejected(self):
        payload = self.selection()
        for command, data in [
            ("parse", {"text": ""}),
            ("add", payload | {"qn": 999}),
            ("add", payload | {"indices": [-1]}),
            ("add", payload | {"indices": [True]}),
            ("add", payload | {"quality_index": 10}),
            ("add", payload | {"media_id": "fake"}),
            ("run_shell", {"command": "calc"}),
            ("open_location", {"kind": "play", "path": "calc.exe"}),
        ]:
            with self.subTest(command=command, data=data):
                self.assertFalse(self.bridge.invoke(command, data)["ok"])
        self.rt.manager.create_tasks.assert_not_called()

    def test_quality_cache_has_account_scope_and_refresh_bypasses_it(self):
        payload = self.selection() | {"index": 0}
        self.invoke("qualities", payload)
        self.invoke("qualities", payload)
        self.assertEqual(1, self.rt.api.get_quality_options.call_count)
        self.rt.auth_version += 1
        self.invoke("qualities", payload)
        self.assertEqual(2, self.rt.api.get_quality_options.call_count)
        self.invoke("qualities", payload | {"refresh": True})
        self.rt.api.clear_stream_cache.assert_called_once()
        self.assertEqual(3, self.rt.api.get_quality_options.call_count)

    def test_settings_validate_numbers_and_never_replace_account(self):
        for data in [
            {"speed_limit_mb": float("nan")},
            {"max_concurrent_tasks": 0},
            {"max_concurrent_tasks": 1.5},
            {"sessdata": "new"},
            {"animations_enabled": "false"},
        ]:
            self.assertFalse(self.bridge.invoke("save_settings", data)["ok"])
        config = self.invoke("save_settings", {"speed_limit_mb": 3.5, "animations_enabled": False})
        self.assertEqual(3.5, config["speed_limit_mb"])
        self.assertEqual("private-session-token", self.rt.config.sessdata)
        self.rt.config_store.save.assert_called_once()

    def test_task_operations_pass_only_selected_ids_and_file_actions_use_known_records(self):
        self.rt.manager.cancel_task.return_value = True
        self.assertEqual(2, self.invoke("task_action", {"action": "cancel", "ids": ["a", "a", "b"]})["count"])
        self.assertEqual(2, self.rt.manager.cancel_task.call_count)
        self.assertFalse(self.bridge.invoke("task_action", {"action": "cancel", "ids": "a"})["ok"])
        self.rt.manager.snapshot.return_value["tasks"] = [
            {"task_id": "a", "status": "completed", "output_path": "calc.exe", "save_dir": self.temp.name}
        ]
        with patch("bili_client.desktop.bridge.os.startfile", create=True) as start:
            self.assertFalse(self.bridge.invoke("open_location", {"task_id": "a", "kind": "play"})["ok"])
            start.assert_not_called()

    def test_cancelled_qr_cannot_save_late_login_result(self):
        self.rt.auth.create_qr_login.return_value = QrLoginInfo("https://passport.bilibili.com/test", "key")
        info = self.invoke("qr_start")

        def poll(_key, save=False):
            self.invoke("qr_cancel")
            return QrPollResult(0, "ok", AuthSession(cookies={"SESSDATA": "new"}))

        self.rt.auth.poll_qr_login.side_effect = poll
        self.assertEqual(86038, self.invoke("qr_poll", {"version": info["version"]})["code"])
        self.rt.auth.save_session.assert_not_called()

    def test_boot_and_shutdown_reject_actions_and_html_is_self_contained(self):
        self.rt.ready.clear()
        self.assertFalse(self.invoke("state")["ready"])
        self.assertFalse(self.bridge.invoke("parse", {"text": "BV1xx411c7mD"})["ok"])
        self.rt.ready.set()
        self.rt.closed.set()
        self.assertFalse(self.bridge.invoke("add", {})["ok"])
        html = asset_html()
        self.assertNotIn("/* APP_SCRIPT */", html)
        self.assertNotIn("/* APP_STYLES */", html)
        self.assertIn("connect-src 'none'", html)
        self.assertNotIn('<script src="http', html)

    def test_window_close_during_qr_poll_cannot_save_a_late_login(self):
        self.rt.auth.create_qr_login.return_value = QrLoginInfo("https://passport.bilibili.com/test", "key")
        info = self.invoke("qr_start")

        def poll(_key, save=False):
            self.rt.closed.set()
            return QrPollResult(0, "ok", AuthSession(cookies={"SESSDATA": "late"}))

        self.rt.auth.poll_qr_login.side_effect = poll
        result = self.bridge.invoke("qr_poll", {"version": info["version"]})
        self.assertEqual(86038, result["data"]["code"])
        self.rt.auth.save_session.assert_not_called()

    def test_fixed_runtime_is_resolved_from_bundle_without_system_install(self):
        directory = Path(self.temp.name) / "webview2"
        directory.mkdir()
        (directory / "msedgewebview2.exe").touch()
        with patch("bili_client.desktop.window.sys._MEIPASS", self.temp.name, create=True):
            self.assertEqual(directory.resolve(), fixed_runtime_path())


if __name__ == "__main__":
    unittest.main()
