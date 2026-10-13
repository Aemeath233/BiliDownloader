from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from collections.abc import Callable

import httpx

from bili_client.core.models import EpisodeInfo, MediaParseResult, QualityOption, StreamInfo, safe_filename
from bili_client.core.security import SHARE_HOSTS, normalize_media_url, normalize_share_url
from bili_client.services.http_client import HttpClient


class BiliApiError(RuntimeError):
    pass


class QualityUnavailableError(BiliApiError):
    pass


class BilibiliApi:
    URL_PATTERN = re.compile(r"https?://[-a-zA-Z0-9+&@#/%?=~_|!:,.;]+")
    REQUEST_RETRIES = 2
    RETRY_STATUS_CODES = {412, 429, 500, 502, 503, 504}
    METADATA_CACHE_SECONDS = 300
    PLAYURL_CACHE_SECONDS = 45
    RESOLVE_CACHE_SECONDS = 300
    MAX_CACHE_ENTRIES = 512

    def __init__(self, http_client: HttpClient, logger: Callable[[str], None] | None = None) -> None:
        self.http_client = http_client
        self.logger = logger or (lambda _message: None)
        self._cache_lock = threading.RLock()
        self._json_cache: dict[tuple[str, str, str], tuple[float, dict]] = {}
        self._resolved_url_cache: dict[tuple[str, str], tuple[float, str]] = {}

    def extract_url(self, text: str) -> str | None:
        if not text:
            return None
        match = self.URL_PATTERN.search(text.strip())
        if match:
            return match.group(0).rstrip(".,;!)]}")
        identifier = re.search(r"(?<![A-Za-z0-9])(?:BV[0-9A-Za-z]{10}|ep\d+|ss\d+)(?![A-Za-z0-9])", text)
        if identifier:
            value = identifier.group(0)
            route = "video" if value.startswith("BV") else "bangumi/play"
            return f"https://www.bilibili.com/{route}/{value}"
        return None

    def parse_media(self, text: str, sessdata: str = "") -> MediaParseResult:
        share_url = self.extract_url(text)
        if not share_url:
            raise BiliApiError("未识别到可用的 B 站链接")
        try:
            share_url = normalize_share_url(share_url)
        except ValueError as exc:
            raise BiliApiError(str(exc)) from exc

        normalized_url = self._resolve_url(share_url, sessdata)

        ep_match = re.search(r"ep(\d+)", normalized_url)
        ss_match = re.search(r"ss(\d+)", normalized_url)
        bv_match = re.search(r"BV([0-9A-Za-z]{10})", normalized_url)

        default_episode_index = 0
        title = "Bilibili 视频"
        folder_title = ""
        source_name = ""
        media_type = "video"
        episodes: list[EpisodeInfo] = []

        if ep_match or ss_match:
            media_type = "bangumi"
            if ep_match:
                ep_id = ep_match.group(1)
                api_url = f"https://api.bilibili.com/pgc/view/web/season?ep_id={ep_id}"
            else:
                ep_id = None
                api_url = f"https://api.bilibili.com/pgc/view/web/season?season_id={ss_match.group(1)}"

            data = self._load_json(
                api_url,
                sessdata,
                referer=normalized_url,
                cache_seconds=self.METADATA_CACHE_SECONDS,
            )
            result = data["result"]
            title = result.get("season_title", title)
            folder_title = title
            source_name = title
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
            data = self._load_json(
                api_url,
                sessdata,
                referer=normalized_url,
                cache_seconds=self.METADATA_CACHE_SECONDS,
            )
            result = data["data"]
            title = result.get("title", title)
            owner = result.get("owner") or {}
            source_name = str(owner.get("name") or "")
            folder_title = source_name or title
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
            folder_title=safe_filename(folder_title or title),
            source_name=source_name,
            media_type=media_type,
        )

    def get_quality_options(self, episode: EpisodeInfo, sessdata: str = "") -> list[QualityOption]:
        data = self._playurl(episode, 127, sessdata)
        play_data = data.get("result", data.get("data", {}))
        self._raise_if_preview_only(play_data)
        descriptions = play_data.get("accept_description", [])
        qualities = play_data.get("accept_quality", [])

        labels = {int(qn): str(label) for label, qn in zip(descriptions, qualities, strict=False)}
        for item in play_data.get("support_formats", []):
            qn = int(item.get("quality") or 0)
            description = item.get("new_description") or item.get("description")
            if qn and description:
                labels.setdefault(qn, str(description))

        available = {
            int(item.get("id") or 0)
            for item in (play_data.get("dash") or {}).get("video", [])
            if item.get("baseUrl") or item.get("base_url")
        }
        if not available and (play_data.get("durl") or play_data.get("durls")):
            available = {int(play_data.get("quality") or 0)}
        available.discard(0)
        if not available:
            raise BiliApiError("当前账号没有获取到可下载的画质，请确认登录和视频访问权限后重试")

        verified = sorted(available, reverse=True)
        highest = labels.get(verified[0], f"QN {verified[0]}")
        restricted = [label for qn, label in labels.items() if qn not in available]
        note = f"当前分集最高可下载：{highest}。仅列出实际可获取的画质。"
        if restricted:
            note += f"\n当前不可获取：{'、'.join(restricted)}（受登录、会员或视频权限影响）。"
        return [QualityOption(qn=qn, label=labels.get(qn, f"QN {qn}"), availability_note=note) for qn in verified]

    @staticmethod
    def check_requested_quality(requested_qn: int, actual_qn: int, labels: dict[int, str]) -> None:
        if requested_qn == actual_qn:
            return
        requested = labels.get(requested_qn, f"QN {requested_qn}")
        actual = labels.get(actual_qn, f"QN {actual_qn}") if actual_qn else "未知画质"
        raise QualityUnavailableError(
            f"所选画质“{requested}”当前不可下载，接口实际返回“{actual}”。"
            "任务已停止，未自动降档；请重新读取画质后选择当前可下载档位。"
        )

    def get_streams(self, episode: EpisodeInfo, qn: int, sessdata: str = "") -> tuple[StreamInfo, StreamInfo | None]:
        data = self._playurl(episode, qn, sessdata)
        play_data = data.get("result", data.get("data", {}))
        self._raise_if_preview_only(play_data)
        dash = play_data.get("dash")
        referer = self._episode_referer(episode)
        if not dash:
            return self._streams_from_durl(play_data, sessdata, referer, requested_qn=qn)

        video_candidates = dash.get("video", [])
        audio_candidates = dash.get("audio", [])
        if not video_candidates:
            raise BiliApiError("视频流为空")

        selected_video = next(
            (item for item in video_candidates if int(item.get("id", 0)) == int(qn)), video_candidates[0]
        )
        actual_qn = int(selected_video.get("id", 0) or 0)
        quality_labels = {
            int(quality): str(description)
            for description, quality in zip(
                play_data.get("accept_description", []),
                play_data.get("accept_quality", []),
                strict=False,
            )
        }
        actual_label = quality_labels.get(actual_qn, f"QN {actual_qn}" if actual_qn else "实际画质")
        self.check_requested_quality(qn, actual_qn, quality_labels)
        video_urls = self._pick_stream_urls(selected_video)
        video_url = video_urls[0]
        video_size = self._probe_size(video_url, sessdata, referer=referer)
        video_stream = StreamInfo(
            kind="video",
            url=video_url,
            size_bytes=video_size,
            backup_urls=video_urls[1:],
            referer=referer,
            quality_qn=actual_qn,
            quality_label=actual_label,
        )

        audio_stream = None
        if audio_candidates:
            selected_audio = audio_candidates[0]
            audio_urls = self._pick_stream_urls(selected_audio)
            audio_url = audio_urls[0]
            audio_size = self._probe_size(audio_url, sessdata, referer=referer)
            audio_stream = StreamInfo(
                kind="audio",
                url=audio_url,
                size_bytes=audio_size,
                backup_urls=audio_urls[1:],
                referer=referer,
            )

        return video_stream, audio_stream

    def _streams_from_durl(
        self,
        play_data: dict,
        sessdata: str,
        referer: str,
        requested_qn: int | None = None,
    ) -> tuple[StreamInfo, StreamInfo | None]:
        durl_items = play_data.get("durl") or play_data.get("durls") or []
        if not durl_items:
            raise BiliApiError("未获取到音视频流地址，可能受版权限制或需要更高权限")
        self._raise_if_preview_only(play_data)

        if len(durl_items) > 1:
            raise BiliApiError("当前视频返回了分段 MP4 流，暂不支持自动合并这些分段")

        item = durl_items[0]
        url = str(item.get("url") or "")
        if not url:
            raise BiliApiError("MP4 流地址为空")

        backups = item.get("backup_url") or item.get("backupUrl") or []
        urls = list(
            dict.fromkeys(normalize_media_url(value) for value in [url, *(str(backup) for backup in backups if backup)])
        )
        try:
            size_bytes = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size_bytes = 0
        if size_bytes <= 0:
            size_bytes = self._probe_size(urls[0], sessdata, referer=referer)

        actual_qn = int(play_data.get("quality") or 0)
        quality_labels = {
            int(quality): str(description)
            for description, quality in zip(
                play_data.get("accept_description", []),
                play_data.get("accept_quality", []),
                strict=False,
            )
        }
        actual_label = quality_labels.get(actual_qn, str(play_data.get("format") or "MP4").upper())
        if requested_qn is not None:
            self.check_requested_quality(requested_qn, actual_qn, quality_labels)

        return (
            StreamInfo(
                kind="video",
                url=urls[0],
                size_bytes=size_bytes,
                backup_urls=urls[1:],
                referer=referer,
                quality_qn=actual_qn,
                quality_label=actual_label,
            ),
            None,
        )

    def _raise_if_preview_only(self, play_data: dict) -> None:
        try:
            error_code = int(play_data.get("error_code") or 0)
        except (TypeError, ValueError):
            error_code = 0
        if play_data.get("is_preview") or error_code == -10403:
            raise BiliApiError("当前账号只能获取预览片段，完整内容可能需要会员、购片或版权权限")

    def _resolve_url(self, url: str, sessdata: str) -> str:
        cache_key = (self._auth_cache_scope(sessdata), url)
        cached_url = self._get_resolved_url_cache(cache_key)
        if cached_url:
            return cached_url

        try:
            response = self.http_client.head(
                url,
                sessdata=sessdata,
                retries=1,
                retry_status_codes=self.RETRY_STATUS_CODES,
                allowed_hosts=SHARE_HOSTS,
            )
            resolved_url = self._validated_redirect(response.url)
            self._set_resolved_url_cache(cache_key, resolved_url, self.RESOLVE_CACHE_SECONDS)
            return resolved_url
        except httpx.HTTPError:
            try:
                response = self.http_client.get(
                    url,
                    sessdata=sessdata,
                    retries=1,
                    retry_status_codes=self.RETRY_STATUS_CODES,
                    allowed_hosts=SHARE_HOSTS,
                )
                resolved_url = self._validated_redirect(response.url)
                self._set_resolved_url_cache(cache_key, resolved_url, self.RESOLVE_CACHE_SECONDS)
                return resolved_url
            except httpx.HTTPError as get_error:
                raise BiliApiError(f"链接解析失败: {get_error}") from get_error

    @staticmethod
    def _validated_redirect(url) -> str:
        try:
            return normalize_share_url(str(url))
        except ValueError as exc:
            raise BiliApiError("链接跳转到了不支持的站点，已停止解析") from exc

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
        return self._load_json(
            api_url,
            sessdata,
            referer=self._episode_referer(episode),
            cache_seconds=self.PLAYURL_CACHE_SECONDS,
        )

    def _load_json(self, url: str, sessdata: str, referer: str = "", cache_seconds: int = 0) -> dict:
        cache_key = (self._auth_cache_scope(sessdata), referer, url)
        if cache_seconds > 0:
            cached = self._get_json_cache(cache_key)
            if cached is not None:
                return cached

        try:
            data = self.http_client.get_json(
                url,
                sessdata=sessdata,
                extra_headers=self._web_headers(referer),
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
        if cache_seconds > 0:
            self._set_json_cache(cache_key, data, cache_seconds)
        return data

    def _pick_stream_urls(self, payload: dict) -> list[str]:
        urls: list[str] = []
        for key in ("baseUrl", "base_url"):
            if payload.get(key):
                urls.append(payload[key])

        backups = payload.get("backupUrl") or payload.get("backup_url") or []
        urls.extend(str(item) for item in backups if item)
        deduped_urls = list(dict.fromkeys(normalize_media_url(url) for url in urls))
        if deduped_urls:
            return deduped_urls
        raise BiliApiError("流媒体地址为空")

    def _probe_size(self, url: str, sessdata: str, referer: str = "") -> int:
        headers = self._web_headers(referer)
        try:
            response = self.http_client.head(
                url,
                sessdata=sessdata,
                extra_headers=headers,
                retries=1,
                retry_status_codes=self.RETRY_STATUS_CODES,
            )
            content_length = response.headers.get("content-length")
            if content_length:
                return int(content_length)
        except (ValueError, httpx.HTTPError):
            pass

        try:
            with self.http_client.stream(
                url,
                sessdata=sessdata,
                extra_headers={**headers, "Range": "bytes=0-0"},
            ) as response:
                content_range = response.headers.get("content-range", "")
                match = re.search(r"/(\d+)$", content_range)
                if match:
                    return int(match.group(1))
                content_length = response.headers.get("content-length")
                if content_length:
                    return int(content_length)
        except (ValueError, httpx.HTTPError):
            pass

        return 0

    def _episode_referer(self, episode: EpisodeInfo) -> str:
        if episode.bvid:
            return f"https://www.bilibili.com/video/{episode.bvid}"
        return "https://www.bilibili.com/"

    def _web_headers(self, referer: str = "") -> dict[str, str]:
        return {"Referer": referer.strip() or "https://www.bilibili.com/"}

    def _auth_cache_scope(self, sessdata: str) -> str:
        token = sessdata.strip()
        if not token:
            return "guest"
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
        return f"auth:{digest}"

    def _get_json_cache(self, key: tuple[str, str, str]) -> dict | None:
        with self._cache_lock:
            item = self._json_cache.get(key)
            if item is None:
                return None
            expires_at, payload = item
            if expires_at <= time.monotonic():
                self._json_cache.pop(key, None)
                return None
            return copy.deepcopy(payload)

    def _set_json_cache(self, key: tuple[str, str, str], payload: dict, ttl_seconds: int) -> None:
        with self._cache_lock:
            self._json_cache[key] = (time.monotonic() + ttl_seconds, copy.deepcopy(payload))
            while len(self._json_cache) > self.MAX_CACHE_ENTRIES:
                self._json_cache.pop(next(iter(self._json_cache)))

    def _get_resolved_url_cache(self, key: tuple[str, str]) -> str | None:
        with self._cache_lock:
            item = self._resolved_url_cache.get(key)
            if item is None:
                return None
            expires_at, resolved_url = item
            if expires_at <= time.monotonic():
                self._resolved_url_cache.pop(key, None)
                return None
            return resolved_url

    def _set_resolved_url_cache(self, key: tuple[str, str], resolved_url: str, ttl_seconds: int) -> None:
        with self._cache_lock:
            self._resolved_url_cache[key] = (time.monotonic() + ttl_seconds, resolved_url)
            while len(self._resolved_url_cache) > self.MAX_CACHE_ENTRIES:
                self._resolved_url_cache.pop(next(iter(self._resolved_url_cache)))
