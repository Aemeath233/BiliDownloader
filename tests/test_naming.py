from __future__ import annotations

import unittest

from bili_client.core.models import EpisodeInfo, QualityOption
from bili_client.core.naming import render_filename_template


class NamingTests(unittest.TestCase):
    def test_render_filename_template_uses_known_fields(self) -> None:
        episode = EpisodeInfo(title="第1话/开场", bvid="BV1xx411c7mD", cid=123, is_bangumi=True)
        quality = QualityOption(qn=80, label="1080P")

        filename = render_filename_template(
            "{index}-{media}-{title}-{quality}",
            media_title="番剧名",
            episode=episode,
            quality=quality,
            index=1,
            total=12,
        )

        self.assertEqual("01-番剧名-第1话_开场-1080P", filename)

    def test_invalid_template_falls_back_to_title(self) -> None:
        episode = EpisodeInfo(title="demo", bvid="BV1xx411c7mD", cid=123, is_bangumi=False)
        quality = QualityOption(qn=80, label="1080P")

        filename = render_filename_template(
            "{title",
            media_title="media",
            episode=episode,
            quality=quality,
            index=1,
            total=1,
        )

        self.assertEqual("demo", filename)


if __name__ == "__main__":
    unittest.main()
