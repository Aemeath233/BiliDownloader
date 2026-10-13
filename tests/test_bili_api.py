from __future__ import annotations

import unittest
from contextlib import contextmanager

from bili_client.core.models import EpisodeInfo
from bili_client.services.bili_api import BiliApiError, BilibiliApi


class DummyHttpClient:
    pass


class CountingHttpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def get_json(
        self,
        url: str,
        sessdata: str = "",
        extra_headers: dict[str, str] | None = None,
        **_kwargs,
    ) -> dict:
        self.calls.append((url, extra_headers))
        return {
            "code": 0,
            "data": {
                "accept_description": ["1080P"],
                "accept_quality": [80],
                "dash": {"video": [{"id": 80, "baseUrl": "https://video-primary"}]},
            },
        }


class StubBiliApi(BilibiliApi):
    def _playurl(self, episode: EpisodeInfo, qn: int, sessdata: str) -> dict:  # type: ignore[override]
        return {
            "data": {
                "dash": {
                    "video": [
                        {
                            "id": qn,
                            "baseUrl": "https://video-primary",
                            "backupUrl": ["https://video-backup", "https://video-primary"],
                        }
                    ],
                    "audio": [
                        {
                            "id": 30280,
                            "baseUrl": "https://audio-primary",
                            "backupUrl": ["https://audio-backup"],
                        }
                    ],
                }
            }
        }

    def _probe_size(self, url: str, sessdata: str, referer: str = "") -> int:  # type: ignore[override]
        return 123 if "video" in url else 45


class StubDurlBiliApi(BilibiliApi):
    def _playurl(self, episode: EpisodeInfo, qn: int, sessdata: str) -> dict:  # type: ignore[override]
        return {
            "result": {
                "quality": 32,
                "format": "mp4",
                "durl": [
                    {
                        "url": "https://mp4-primary",
                        "backup_url": ["https://mp4-backup", "https://mp4-primary"],
                        "size": 456,
                    }
                ],
            }
        }


class StubPreviewDurlBiliApi(BilibiliApi):
    def _playurl(self, episode: EpisodeInfo, qn: int, sessdata: str) -> dict:  # type: ignore[override]
        return {
            "result": {
                "is_preview": 1,
                "error_code": -10403,
                "durl": [{"url": "https://preview-only", "size": 123}],
            }
        }


class StubLowerQualityBiliApi(BilibiliApi):
    def _playurl(self, episode: EpisodeInfo, qn: int, sessdata: str) -> dict:  # type: ignore[override]
        return {
            "data": {
                "accept_description": ["720P"],
                "accept_quality": [64],
                "dash": {"video": [{"id": 64, "baseUrl": "https://video-720"}], "audio": []},
            }
        }

    def _probe_size(self, url: str, sessdata: str, referer: str = "") -> int:  # type: ignore[override]
        return 0


class ProbeHttpClient:
    def __init__(self) -> None:
        self.stream_calls = 0

    def head(self, *_args, **_kwargs):
        return type("Response", (), {"headers": {}})()

    @contextmanager
    def stream(self, *_args, **_kwargs):
        self.stream_calls += 1
        yield type("Response", (), {"headers": {"content-length": str(2 * 1024 * 1024)}})()


class BiliApiTests(unittest.TestCase):
    def test_bare_ids_and_share_punctuation_are_recognized(self) -> None:
        api = BilibiliApi(DummyHttpClient())
        self.assertEqual("https://www.bilibili.com/video/BV1xx411c7mD", api.extract_url("BV1xx411c7mD"))
        self.assertEqual("https://www.bilibili.com/bangumi/play/ep1234", api.extract_url("ep1234"))
        self.assertEqual("https://b23.tv/demo", api.extract_url("分享：https://b23.tv/demo。"))
        self.assertEqual("https://b23.tv/demo", api.extract_url("(https://b23.tv/demo)."))

    def test_pick_stream_urls_deduplicates(self) -> None:
        api = BilibiliApi(http_client=DummyHttpClient())  # type: ignore[arg-type]

        urls = api._pick_stream_urls(
            {
                "baseUrl": "https://primary",
                "backupUrl": ["https://backup", "https://primary"],
            }
        )

        self.assertEqual(["https://primary", "https://backup"], urls)

    def test_get_streams_keeps_backup_urls(self) -> None:
        api = StubBiliApi(http_client=DummyHttpClient())  # type: ignore[arg-type]
        episode = EpisodeInfo(title="demo", bvid="BV1xx411c7mD", cid=123, is_bangumi=False)

        video_stream, audio_stream = api.get_streams(episode, qn=80)

        self.assertEqual("https://video-primary", video_stream.url)
        self.assertEqual(["https://video-backup"], video_stream.backup_urls)
        self.assertIsNotNone(audio_stream)
        assert audio_stream is not None
        self.assertEqual("https://audio-primary", audio_stream.url)
        self.assertEqual(["https://audio-backup"], audio_stream.backup_urls)

    def test_get_streams_rejects_fallback_quality(self) -> None:
        api = StubLowerQualityBiliApi(http_client=DummyHttpClient())  # type: ignore[arg-type]
        episode = EpisodeInfo(title="demo", bvid="BV1xx411c7mD", cid=123, is_bangumi=False)

        with self.assertRaisesRegex(BiliApiError, "实际返回.*720P"):
            api.get_streams(episode, qn=80)

    def test_get_streams_falls_back_to_single_durl_stream(self) -> None:
        api = StubDurlBiliApi(http_client=DummyHttpClient())  # type: ignore[arg-type]
        episode = EpisodeInfo(title="movie", bvid="BV1xx411c7mD", cid=123, is_bangumi=True)

        video_stream, audio_stream = api.get_streams(episode, qn=32)

        self.assertEqual("https://mp4-primary", video_stream.url)
        self.assertEqual(["https://mp4-backup"], video_stream.backup_urls)
        self.assertEqual(456, video_stream.size_bytes)
        self.assertEqual("https://www.bilibili.com/video/BV1xx411c7mD", video_stream.referer)
        self.assertIsNone(audio_stream)

    def test_get_streams_rejects_preview_only_durl(self) -> None:
        api = StubPreviewDurlBiliApi(http_client=DummyHttpClient())  # type: ignore[arg-type]
        episode = EpisodeInfo(title="movie", bvid="BV1xx411c7mD", cid=123, is_bangumi=True)

        with self.assertRaisesRegex(BiliApiError, "预览"):
            api.get_streams(episode, qn=80)

    def test_get_quality_options_rejects_preview_only_result(self) -> None:
        api = StubPreviewDurlBiliApi(http_client=DummyHttpClient())  # type: ignore[arg-type]
        episode = EpisodeInfo(title="movie", bvid="BV1xx411c7mD", cid=123, is_bangumi=True)

        with self.assertRaisesRegex(BiliApiError, "预览"):
            api.get_quality_options(episode, sessdata="sess")

    def test_playurl_requests_use_video_referer_and_short_cache(self) -> None:
        http_client = CountingHttpClient()
        api = BilibiliApi(http_client=http_client)  # type: ignore[arg-type]
        episode = EpisodeInfo(title="demo", bvid="BV1xx411c7mD", cid=123, is_bangumi=False)

        first = api.get_quality_options(episode, sessdata="sess")
        second = api.get_quality_options(episode, sessdata="sess")

        self.assertEqual([80], [item.qn for item in first])
        self.assertEqual([80], [item.qn for item in second])
        self.assertEqual(1, len(http_client.calls))
        self.assertEqual("https://www.bilibili.com/video/BV1xx411c7mD", http_client.calls[0][1]["Referer"])

    def test_probe_size_does_not_consume_response_body(self) -> None:
        http_client = ProbeHttpClient()
        api = BilibiliApi(http_client=http_client)  # type: ignore[arg-type]

        size = api._probe_size("https://media.example/video", "")

        self.assertEqual(2 * 1024 * 1024, size)
        self.assertEqual(1, http_client.stream_calls)


if __name__ == "__main__":
    unittest.main()
