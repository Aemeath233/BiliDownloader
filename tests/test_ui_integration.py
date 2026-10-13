from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bili_client.core.models import AppConfig, EpisodeInfo, MediaParseResult, QualityOption
from bili_client.ui.main_window import MainWindow


def task_snapshot(index: int, status: str = "completed") -> dict:
    return dict(
        task_id=str(index),
        title=f"测试视频 {index}",
        quality_label="1080P",
        status=status,
        save_dir="D:/Downloads",
        output_path=f"D:/Downloads/video-{index}.mp4",
        percent=100 if status == "completed" else 20,
        total_bytes=1000,
        downloaded_bytes=1000 if status == "completed" else 200,
        speed_bps=100,
        message="已完成" if status == "completed" else "下载中",
        error="",
        queue_position=None,
        can_retry=False,
        can_remove=status == "completed",
        can_cancel=status == "downloading",
        cancel_requested=False,
    )


class UiIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.metrics = dict(
            active_count=0, queued_count=0, completed_count=1000, failed_count=0, canceled_count=0, total_speed_bps=0
        )
        self.manager = Mock()
        self.manager.snapshot.return_value = {"metrics": self.metrics, "tasks": [task_snapshot(i) for i in range(1000)]}
        self.manager.metrics.return_value = self.metrics
        self.manager.has_unfinished_tasks.return_value = False
        self.app = MainWindow(
            manager=self.manager,
            api=Mock(),
            ffmpeg=Mock(),
            config_store=Mock(),
            initial_config=AppConfig(animations_enabled=False, save_path=self.temp.name),
            log_file_path=Path(self.temp.name) / "test.log",
            log_dir_path=Path(self.temp.name),
        )
        self.app.withdraw()
        self.callback_errors = []
        self.app.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.addCleanup(self.app.destroy)

    def pump(self, duration: float = 0.08) -> None:
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.002)
        self.assertEqual([], self.callback_errors)

    def wait_for_cards(self, page, count: int) -> None:
        deadline = time.monotonic() + 5
        while len(page.task_cards) != count and time.monotonic() < deadline:
            self.pump(0.02)
        self.assertEqual(count, len(page.task_cards))

    def test_shell_renders_before_pages_and_rapid_navigation_uses_latest_request(self) -> None:
        self.assertEqual({}, self.app.pages)
        self.assertFalse(self.app._ready)
        self.pump()
        self.assertEqual({"parse"}, set(self.app.pages))
        self.app.show_page("settings")
        self.app.show_page("logs")
        self.app.show_page("queue")
        self.pump()
        self.assertEqual({"parse", "queue"}, set(self.app.pages))
        self.assertEqual("queue", self.app.current_page)
        self.assertIs(self.app.pages["queue"], self.app._displayed_page)

    def test_large_queue_has_bounded_widgets_and_search_returns_matches(self) -> None:
        self.pump()
        self.app.show_page("queue")
        self.pump()
        page = self.app.pages["queue"]
        self.wait_for_cards(page, page.PAGE_SIZE)
        self.assertEqual(page.PAGE_SIZE, len(page.task_cards))
        page._change_page(1)
        self.wait_for_cards(page, page.PAGE_SIZE)
        self.assertEqual(page.PAGE_SIZE, len(page.task_cards))
        self.assertEqual("30", next(iter(page.task_cards)))
        page.search_entry.insert(0, "video-999.mp4")
        page._apply_search()
        self.wait_for_cards(page, 1)
        self.assertEqual({"999"}, set(page.task_cards))
        self.assertEqual(0, page._current_page)

    def test_progress_updates_do_not_redraw_unchanged_titles_or_actions(self) -> None:
        self.pump()
        self.app.show_page("queue")
        self.pump()
        card = self.app.pages["queue"].task_cards["0"]
        self.assertIs(card.title_label._label, card.title_label.winfo_children()[-1])
        with (
            patch.object(card.title_label, "configure") as title_update,
            patch.object(card.cancel_button, "configure") as action_update,
        ):
            task = task_snapshot(0)
            task["speed_bps"] += 10
            card.update_card(task)
            title_update.assert_not_called()
            action_update.assert_not_called()

    def test_hidden_logs_defer_rendering_and_filtered_append_is_incremental(self) -> None:
        self.pump()
        self.app.show_page("logs")
        self.pump()
        page = self.app.pages["logs"]
        self.app.show_page("parse")
        with patch.object(page.log_text, "insert") as insert:
            page.append_messages(["下载成功", "下载失败"])
            insert.assert_not_called()
        self.app.show_page("logs")
        self.assertIn("下载失败", page.log_text.get("1.0", "end"))
        page.filter_segment.set("错误")
        page._render_visible_logs()
        with patch.object(page.log_text, "delete") as delete:
            page.append_messages(["下载成功 2", "下载失败 2"])
            delete.assert_not_called()
        text = page.log_text.get("1.0", "end")
        self.assertNotIn("成功", text)
        self.assertIn("失败 2", text)

    def test_animation_preference_survives_form_save(self) -> None:
        self.pump()
        self.app.show_page("settings")
        self.pump()
        page = self.app.pages["settings"]
        self.assertFalse(page.animations_enabled_var.get())
        page.animations_enabled_var.set(True)
        self.assertTrue(page._collect_form_config().animations_enabled)

    def test_cached_episode_change_rejects_older_quality_callback(self) -> None:
        self.pump()
        page = self.app.add_task_page
        media = MediaParseResult(
            "https://b23.tv/demo",
            "https://www.bilibili.com/video/BV1xx411c7mD",
            "demo",
            [EpisodeInfo("first", "BV1xx411c7mD", 1, False), EpisodeInfo("second", "BV1xx411c7mD", 2, False)],
        )
        page.current_media = media
        page._populate_episode_selector(media)
        with patch.object(page.ui_tasks, "run"):
            page._load_qualities_for_episode(0)
        stale_token = page._quality_request_token
        page.quality_cache[1] = [QualityOption(64, "720P")]
        page._load_qualities_for_episode(1)
        page._on_quality_success(stale_token, [QualityOption(80, "1080P")])
        self.assertEqual([64], [quality.qn for quality in page.current_qualities])
        self.assertEqual("720P", page.quality_combo.get())

    def test_large_episode_list_retains_selection_with_only_visible_tk_variables(self) -> None:
        self.pump()
        page = self.app.add_task_page
        media = MediaParseResult(
            "https://b23.tv/demo",
            "https://www.bilibili.com/video/BV1xx411c7mD",
            "demo",
            [EpisodeInfo(f"part-{index}", "BV1xx411c7mD", index + 1, False) for index in range(1000)],
        )
        page.current_media = media
        page._populate_episode_selector(media)
        page.episode_selection_vars[3].set(True)
        page._change_episode_page(99)
        self.assertTrue(page.episode_selection_vars[3].get())
        self.assertIsNone(page.episode_selection_vars[3]._variable)
        self.assertEqual(
            page.episode_page_size, sum(state._variable is not None for state in page.episode_selection_vars.values())
        )
        with patch.object(page, "_load_qualities_for_episode"):
            page._select_all_episodes()
        self.assertEqual(1000, len(page._selected_episode_indices()))
        page._change_episode_page(0)
        self.assertTrue(page.episode_selection_vars[3].get())

    def test_clearing_selection_rejects_inflight_quality_result(self) -> None:
        self.pump()
        page = self.app.add_task_page
        media = MediaParseResult(
            "https://b23.tv/demo",
            "https://www.bilibili.com/video/BV1xx411c7mD",
            "demo",
            [EpisodeInfo("first", "BV1xx411c7mD", 1, False)],
        )
        page.current_media = media
        page._populate_episode_selector(media)
        with patch.object(page.ui_tasks, "run"):
            page._load_qualities_for_episode(0)
        stale_token = page._quality_request_token
        page._clear_episode_selection()
        page._on_quality_success(stale_token, [QualityOption(80, "1080P")])
        self.assertEqual([], page.current_qualities)
        self.assertEqual("disabled", page.add_button.cget("state"))

    def test_slow_queue_operation_keeps_event_loop_responsive_and_does_not_switch_pages(self) -> None:
        self.pump()
        import threading

        release = threading.Event()
        self.manager.cancel_task.side_effect = lambda _task_id: release.wait(2)
        started = time.monotonic()
        self.app._cancel_task("demo")
        self.assertLess(time.monotonic() - started, 0.1)
        self.app._cancel_task("demo")
        self.pump()
        self.assertEqual(1, self.manager.cancel_task.call_count)
        release.set()
        self.pump()
        self.assertEqual("parse", self.app.current_page)

    def test_refreshed_account_keeps_newer_user_preferences(self) -> None:
        self.pump()
        self.app.current_config = AppConfig(speed_limit_mb=2.5, max_concurrent_tasks=4)
        self.app._accept_refreshed_session("new-session")
        self.assertEqual(2.5, self.app.current_config.speed_limit_mb)
        self.assertEqual(4, self.app.current_config.max_concurrent_tasks)
        self.assertEqual("new-session", self.app.current_config.sessdata)
