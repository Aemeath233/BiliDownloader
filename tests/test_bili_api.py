from __future__ import annotations

import unittest

from bili_client.core.models import EpisodeInfo
from bili_client.services.bili_api import BilibiliApi


class DummyHttpClient:
    pass


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

    def _probe_size(self, url: str, sessdata: str) -> int:  # type: ignore[override]
        return 123 if "video" in url else 45


class BiliApiTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
