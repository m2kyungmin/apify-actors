"""Parser for public TikTok profile and individual video pages."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36'
    ),
}
STATE_SCRIPT_RE = re.compile(
    r'<script[^>]*\bid=["\'](?:SIGI_STATE|__UNIVERSAL_DATA_FOR_REHYDRATION__)["\'][^>]*>(?P<body>.*?)</script>',
    re.I | re.S,
)
BLOCK_RE = re.compile(r'Please wait|_wafchallengeid|captcha|verify|Access Denied', re.I)
VIDEO_ID_RE = re.compile(r'/video/(\d+)')


class TikTokError(RuntimeError):
    """TikTok did not return a usable public document."""


class TikTokBlocked(TikTokError):
    """TikTok returned a challenge, rate limit, or access block."""


def normalize_username(value: str) -> str:
    username = value.strip().lstrip('@')
    if not username or '/' in username or '?' in username:
        raise ValueError(f'Invalid TikTok username: {value!r}')
    return username


def profile_url(username: str) -> str:
    return f'https://www.tiktok.com/@{normalize_username(username)}'


def normalize_video_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'} or parsed.netloc not in {'www.tiktok.com', 'tiktok.com'}:
        raise ValueError('Each video URL must be a public https://www.tiktok.com/@user/video/<id> URL.')
    match = VIDEO_ID_RE.search(parsed.path)
    if not match:
        raise ValueError('Each video URL must contain /video/<numeric-id>.')
    return f'https://www.tiktok.com{parsed.path}'


def _scope_from_html(html: str) -> dict[str, Any]:
    for match in STATE_SCRIPT_RE.finditer(html):
        try:
            payload = json.loads(match.group('body'))
        except json.JSONDecodeError:
            continue
        scope = payload.get('__DEFAULT_SCOPE__') if isinstance(payload, dict) else None
        if isinstance(scope, dict):
            return scope
    if BLOCK_RE.search(html[:20_000]):
        raise TikTokBlocked('TikTok returned a WAF challenge or verification page.')
    raise TikTokError('TikTok public state JSON was not found in the page response.')


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None and value != '' else None
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> str | None:
    seconds = _as_int(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def parse_profile_html(html: str, username: str) -> dict[str, Any]:
    detail = _scope_from_html(html).get('webapp.user-detail')
    if not isinstance(detail, dict):
        raise TikTokError('TikTok profile state was not found in the public page.')
    if _as_int(detail.get('statusCode')) not in {None, 0}:
        raise TikTokError(f'TikTok profile returned status {detail.get("statusCode")}: {detail.get("statusMsg", "")}')
    info = detail.get('userInfo')
    if not isinstance(info, dict):
        raise TikTokError('TikTok profile did not contain user information.')
    user = info.get('user') if isinstance(info.get('user'), dict) else {}
    stats = info.get('stats') if isinstance(info.get('stats'), dict) else {}
    unique_id = str(user.get('uniqueId') or normalize_username(username))
    return {
        'type': 'profile',
        'username': unique_id,
        'userId': str(user.get('id')) if user.get('id') is not None else None,
        'secUid': user.get('secUid'),
        'nickname': user.get('nickname'),
        'bio': user.get('signature'),
        'verified': bool(user.get('verified')),
        'privateAccount': bool(user.get('privateAccount')),
        'avatarUrl': user.get('avatarLarger') or user.get('avatarMedium') or user.get('avatarThumb'),
        'profileUrl': profile_url(unique_id),
        'followerCount': _as_int(stats.get('followerCount')),
        'followingCount': _as_int(stats.get('followingCount')),
        'heartCount': _as_int(stats.get('heartCount') or stats.get('heart')),
        'videoCount': _as_int(stats.get('videoCount')),
        'friendCount': _as_int(stats.get('friendCount')),
    }


def parse_video_html(html: str, source_url: str) -> dict[str, Any]:
    detail = _scope_from_html(html).get('webapp.video-detail')
    if not isinstance(detail, dict):
        raise TikTokError('TikTok video state was not found in the public page.')
    item_info = detail.get('itemInfo') if isinstance(detail.get('itemInfo'), dict) else {}
    item = item_info.get('itemStruct') if isinstance(item_info.get('itemStruct'), dict) else {}
    if not item:
        raise TikTokError('TikTok video page did not contain a public video record.')
    author = item.get('author') if isinstance(item.get('author'), dict) else {}
    stats = item.get('stats') if isinstance(item.get('stats'), dict) else {}
    video = item.get('video') if isinstance(item.get('video'), dict) else {}
    music = item.get('music') if isinstance(item.get('music'), dict) else {}
    hashtags = [
        entry.get('hashtagName')
        for entry in item.get('textExtra', [])
        if isinstance(entry, dict) and entry.get('hashtagName')
    ]
    video_id = str(item.get('id') or '')
    if not video_id:
        raise TikTokError('TikTok video record has no ID.')
    return {
        'type': 'video',
        'videoId': video_id,
        'url': normalize_video_url(source_url),
        'description': item.get('desc'),
        'hashtags': hashtags,
        'createdAt': _timestamp(item.get('createTime')),
        'authorUsername': author.get('uniqueId'),
        'authorNickname': author.get('nickname'),
        'authorId': str(author.get('id')) if author.get('id') is not None else None,
        'playCount': _as_int(stats.get('playCount')),
        'likeCount': _as_int(stats.get('diggCount')),
        'commentCount': _as_int(stats.get('commentCount')),
        'shareCount': _as_int(stats.get('shareCount')),
        'saveCount': _as_int(stats.get('collectCount')),
        'durationSeconds': _as_int(video.get('duration')),
        'width': _as_int(video.get('width')),
        'height': _as_int(video.get('height')),
        'coverUrl': video.get('cover') or video.get('originCover'),
        'dynamicCoverUrl': video.get('dynamicCover'),
        'musicTitle': music.get('title'),
        'musicAuthor': music.get('authorName'),
        'musicOriginal': music.get('original'),
        'isAd': bool(item.get('isAd')),
    }


class TikTokClient:
    def __init__(self, *, proxy_url: str | None = None) -> None:
        self.proxy_url = proxy_url

    async def _get(self, url: str) -> str:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            proxy=self.proxy_url,
            follow_redirects=True,
            timeout=httpx.Timeout(30),
        ) as client:
            response = await client.get(url)
        if response.status_code in {403, 429, 503}:
            raise TikTokBlocked(f'TikTok returned HTTP {response.status_code}.')
        response.raise_for_status()
        return response.text

    async def get_profile(self, username: str) -> dict[str, Any]:
        normalized = normalize_username(username)
        return parse_profile_html(await self._get(profile_url(normalized)), normalized)

    async def get_video(self, url: str) -> dict[str, Any]:
        normalized = normalize_video_url(url)
        return parse_video_html(await self._get(normalized), normalized)
