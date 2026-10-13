from __future__ import annotations

import re
from string import Formatter

from bili_client.core.models import EpisodeInfo, QualityOption, safe_filename

DEFAULT_FILENAME_TEMPLATE = "{title}"


def render_filename_template(
    template: str,
    *,
    media_title: str,
    episode: EpisodeInfo,
    quality: QualityOption,
    index: int,
    total: int,
) -> str:
    pattern = (template or DEFAULT_FILENAME_TEMPLATE).strip() or DEFAULT_FILENAME_TEMPLATE
    values = {
        "title": episode.title,
        "media": media_title or episode.title,
        "quality": quality.label,
        "bvid": episode.bvid,
        "cid": str(episode.cid),
        "index": f"{index:02d}" if total >= 10 else str(index),
        "total": str(total),
    }

    try:
        if len(pattern) > 512 or any(
            len(spec or "") > 24 or any(int(width) > 180 for width in re.findall(r"\d+", spec or ""))
            for _, _, spec, _ in Formatter().parse(pattern)
        ):
            raise ValueError("文件名模板过长")
        rendered = pattern.format_map(_SafeFormatDict(values))
    except (KeyError, ValueError, AttributeError, IndexError):
        rendered = DEFAULT_FILENAME_TEMPLATE.format_map(_SafeFormatDict(values))

    return safe_filename(rendered)


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
