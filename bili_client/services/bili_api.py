from __future__ import annotations

import json
import re
from typing import Callable

import httpx

from bili_client.core.models import EpisodeInfo, MediaParseResult, QualityOption, StreamInfo, safe_filename
from bili_client.services.http_client import HttpClient


class BiliApiError(RuntimeError):
    pass


class BilibiliApi:
    URL_PATTERN = re.compile(r"https?://[-a-zA-Z0-9+&@#/%?=~_|!:,.;]+")
    REQUEST_RETRIES = 2
    RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(self, http_client: HttpClient, logger: Callable[[str], None] | None = None) -> None:
        self.http_client = http_client
        self.logger = logger or (lambda _message: None)

    def extract_url(self, text: str) -> str | None:
        if not text:
            return None
        match = self.URL_PATTERN.search(text.strip())
        return match.group(0) if match else None

    def parse_media(self, text: str, sessdata: str = "") -> MediaParseResult:
        share_url = self.extract_url(text)
        if not share_url:
            raise BiliApiError("未识别到可用的 B 站链接")

        normalized_url = self._resolve_url(share_url, sessdata)

        ep_match = re.search(r"ep(\d+)", normalized_url)
        ss_match = re.search(r"ss(\d+)", normalized_url)
        bv_match = re.search(r"BV([0-9A-Za-z]{10})", normalized_url)

        default_episode_index = 0
        title = "Bilibili 视频"
        episodes: list[EpisodeInfo] = []

        if ep_match or ss_match:
            if ep_match:
                ep_id = ep_match.group(1)
                api_url = f"https://api.bilibili.com/pgc/view/web/season?ep_id={ep_id}"
            else:
                ep_id = None
                api_url = f"https://api.bilibili.com/pgc/view/web/season?season_id={ss_match.group(1)}"

            data = self._load_json(api_url, sessdata)
            result = data["result"]
            title = result.get("season_title", title)
            items = result.get("episodes", [])

            for index, item in enumerate(items):
                episode_label = f"{item.get('title', '')} {item.get('long_title', '')}".strip()
                full_title = title if len(items) == 1 else f"{title} - {episode_label}".strip(" -")
                episodes.append(
                    EpisodeInfo(
                        title=safe_filename(full_title),
                        bvid=item["bvid"],
                        cid=int(item["cid"]),
                        is_bangumi=True,
                    )
                )
                if ep_id and str(item.get("ep_id")) == ep_id:
                    default_episode_index = index

        elif bv_match:
            bvid = bv_match.group(0)
            api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
            data = self._load_json(api_url, sessdata)
            result = data["data"]
            title = result.get("title", title)
            pages = result.get("pages", [])

            page_match = re.search(r"[?&]p=(\d+)", normalized_url) or re.search(r"[?&]p=(\d+)", text)
            if page_match:
                requested_page = max(1, int(page_match.group(1)))
                default_episode_index = min(requested_page - 1, max(0, len(pages) - 1))

            for page in pages:
                if len(pages) > 1:
                    full_title = f"{title} - P{page['page']} {page['part']}"
                else:
                    full_title = title
                episodes.append(
                    EpisodeInfo(
                        title=safe_filename(full_title),
                        bvid=bvid,
                        cid=int(page["cid"]),
                        is_bangumi=False,
                    )
                )
        else:
            raise BiliApiError("只支持 BV、ep、ss 三类链接")

        if not episodes:
            raise BiliApiError("没有解析到可下载的视频信息")

        return MediaParseResult(
            source_url=share_url,
            normalized_url=normalized_url,
            title=title,
            episodes=episodes,
            default_episode_index=default_episode_index,
        )

    def get_quality_options(self, episode: EpisodeInfo, sessdata: str = "") -> list[QualityOption]:
        data = self._playurl(episode, 116, sessdata)
        play_data = data.get("result", data.get("data", {}))
        descriptions = play_data.get("accept_description", [])
        qualities = play_data.get("accept_quality", [])

        options = [QualityOption(qn=int(qn), label=str(label)) for label, qn in zip(descriptions, qualities)]
        if not options:
            raise BiliApiError("未读取到可用的画质选项")
        return options

    def get_streams(self, episode: EpisodeInfo, qn: int, sessdata: str = "") -> tuple[StreamInfo, StreamInfo | None]:
        data = self._playurl(episode, qn, sessdata)
        play_data = data.get("result", data.get("data", {}))
        dash = play_data.get("dash")
        if not dash:
            raise BiliApiError("未获取到 DASH 流地址，可能受版权限制或需要更高权限")

        video_candidates = dash.get("video", [])
        audio_candidates = dash.get("audio", [])
        if not video_candidates:
            raise BiliApiError("视频流为空")

        selected_video = next((item for item in video_candidates if int(item.get("id", 0)) == int(qn)), video_candidates[0])
        video_urls = self._pick_stream_urls(selected_video)
        video_url = video_urls[0]
        video_size = self._probe_size(video_url, sessdata)
        video_stream = StreamInfo(
            kind="video",
            url=video_url,
            size_bytes=video_size,
            backup_urls=video_urls[1:],
        )

        audio_stream = None
        if audio_candidates:
            selected_audio = audio_candidates[0]
            audio_urls = self._pick_stream_urls(selected_audio)
            audio_url = audio_urls[0]
            audio_size = self._probe_size(audio_url, sessdata)
            audio_stream = StreamInfo(
                kind="audio",
                url=audio_url,
                size_bytes=audio_size,
                backup_urls=audio_urls[1:],
            )

        return video_stream, audio_stream

    def _resolve_url(self, url: str, sessdata: str) -> str:
        try:
            response = self.http_client.head(
                url,
                sessdata=sessdata,
                retries=1,
                retry_status_codes=self.RETRY_STATUS_CODES,
            )
            return str(response.url)
        except httpx.HTTPError as exc:
            try:
                response = self.http_client.get(
                    url,
                    sessdata=sessdata,
                    retries=1,
                    retry_status_codes=self.RETRY_STATUS_CODES,
                )
                return str(response.url)
            except httpx.HTTPError:
                raise BiliApiError(f"链接解析失败: {exc}") from exc

    def _playurl(self, episode: EpisodeInfo, qn: int, sessdata: str) -> dict:
        if episode.is_bangumi:
            api_url = (
                "https://api.bilibili.com/pgc/player/web/playurl"
                f"?bvid={episode.bvid}&cid={episode.cid}&qn={qn}&fnver=0&fnval=4048&fourk=1"
            )
        else:
            api_url = (
                "https://api.bilibili.com/x/player/playurl"
                f"?bvid={episode.bvid}&cid={episode.cid}&qn={qn}&fnver=0&fnval=4048&fourk=1"
            )
        return self._load_json(api_url, sessdata)

    def _load_json(self, url: str, sessdata: str) -> dict:
        try:
            data = self.http_client.get_json(
                url,
                sessdata=sessdata,
                retries=self.REQUEST_RETRIES,
                retry_status_codes=self.RETRY_STATUS_CODES,
            )
        except httpx.HTTPStatusError as exc:
            raise BiliApiError(f"请求失败: HTTP {exc.response.status_code}") from exc
        except json.JSONDecodeError as exc:
            raise BiliApiError("接口返回不是 JSON，可能被风控拦截") from exc
        except httpx.HTTPError as exc:
            raise BiliApiError(f"网络请求失败: {exc}") from exc

        if data.get("code") != 0:
            message = data.get("message") or data.get("msg") or "未知错误"
            raise BiliApiError(message)
        return data

    def _pick_stream_urls(self, payload: dict) -> list[str]:
        urls: list[str] = []
        for key in ("baseUrl", "base_url"):
            if payload.get(key):
                urls.append(payload[key])

        backups = payload.get("backupUrl") or payload.get("backup_url") or []
        urls.extend(str(item) for item in backups if item)
        deduped_urls = list(dict.fromkeys(urls))
        if deduped_urls:
            return deduped_urls
        raise BiliApiError("流媒体地址为空")

    def _probe_size(self, url: str, sessdata: str) -> int:
        try:
            response = self.http_client.head(
                url,
                sessdata=sessdata,
                retries=1,
                retry_status_codes=self.RETRY_STATUS_CODES,
            )
            content_length = response.headers.get("content-length")
            if content_length:
                return int(content_length)
        except (ValueError, httpx.HTTPError):
            pass

        try:
            response = self.http_client.get(
                url,
                sessdata=sessdata,
                extra_headers={"Range": "bytes=0-0"},
                retries=1,
                retry_status_codes=self.RETRY_STATUS_CODES,
            )
            content_range = response.headers.get("content-range", "")
            match = re.search(r"/(\d+)$", content_range)
            if match:
                return int(match.group(1))
        except (ValueError, httpx.HTTPError):
            pass

        return 0
