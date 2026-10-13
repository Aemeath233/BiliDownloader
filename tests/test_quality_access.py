from __future__ import annotations

import unittest
from unittest.mock import Mock

from bili_client.core.errors import humanize_error
from bili_client.core.models import EpisodeInfo
from bili_client.services.bili_api import BiliApiError, BilibiliApi


class PlayurlApi(BilibiliApi):
    def __init__(self, payload):
        super().__init__(Mock())
        self.payload = payload
        self.requested_qns = []

    def _playurl(self, episode, qn, sessdata):
        self.requested_qns.append(qn)
        return {"data": self.payload}


class QualityAccessTests(unittest.TestCase):
    def setUp(self):
        self.episode = EpisodeInfo("demo", "BV1R1e4zKEh1", 1, False)
        self.payload = {
            "quality": 80,
            "accept_quality": [125, 120, 116, 80, 64, 32, 16],
            "accept_description": ["HDR", "4K", "1080P60", "1080P", "720P", "480P", "360P"],
            "dash": {"video": [{"id": qn, "baseUrl": f"https://media.example/{qn}"} for qn in (80, 64, 32, 16)]},
        }

    def test_non_vip_menu_contains_only_actual_returned_streams(self):
        api = PlayurlApi(self.payload)
        options = api.get_quality_options(self.episode, "non-vip-test-session")
        self.assertEqual([80, 64, 32, 16], [option.qn for option in options])
        self.assertIn("最高可下载：1080P", options[0].availability_note)
        self.assertIn("不可获取：HDR、4K、1080P60", options[0].availability_note)
        self.assertEqual([127], api.requested_qns)

    def test_vip_streams_are_retained_if_actually_returned(self):
        self.payload["dash"]["video"].insert(0, {"id": 120, "baseUrl": "https://media.example/4k"})
        options = PlayurlApi(self.payload).get_quality_options(self.episode, "vip-test-session")
        self.assertEqual(120, options[0].qn)
        self.assertEqual("4K", options[0].label)

    def test_metadata_without_urls_cannot_be_advertised_as_downloadable(self):
        self.payload["dash"]["video"] = [{"id": 120}, {"id": 80, "baseUrl": "https://media.example/80"}]
        options = PlayurlApi(self.payload).get_quality_options(self.episode)
        self.assertEqual([80], [option.qn for option in options])

    def test_no_media_streams_cannot_enable_quality_selection(self):
        self.payload.pop("dash")
        with self.assertRaisesRegex(BiliApiError, "没有获取到可下载"):
            PlayurlApi(self.payload).get_quality_options(self.episode)

    def test_mp4_reports_only_actual_quality(self):
        self.payload.pop("dash")
        self.payload["durl"] = [{"url": "https://media.example/80", "size": 100}]
        options = PlayurlApi(self.payload).get_quality_options(self.episode)
        self.assertEqual([80], [option.qn for option in options])

    def test_missing_description_uses_returned_quality_id(self):
        self.payload.pop("accept_quality")
        self.payload.pop("accept_description")
        options = PlayurlApi(self.payload).get_quality_options(self.episode)
        self.assertEqual([80, 64, 32, 16], [option.qn for option in options])

    def test_4k_downgrade_is_rejected_before_media_probing(self):
        api = PlayurlApi(self.payload)
        api._probe_size = Mock()
        with self.assertRaisesRegex(BiliApiError, "4K.*实际返回.*1080P") as raised:
            api.get_streams(self.episode, 120)
        api._probe_size.assert_not_called()
        self.assertIn("未自动降档", humanize_error(str(raised.exception)))

    def test_exact_selected_stream_is_accepted(self):
        api = PlayurlApi(self.payload)
        api._probe_size = Mock(return_value=100)
        video, _ = api.get_streams(self.episode, 64)
        self.assertEqual(64, video.quality_qn)

    def test_mp4_downgrade_is_rejected(self):
        self.payload.pop("dash")
        self.payload["durl"] = [{"url": "https://media.example/80", "size": 100}]
        with self.assertRaisesRegex(BiliApiError, "4K.*实际返回.*1080P"):
            PlayurlApi(self.payload).get_streams(self.episode, 120)
