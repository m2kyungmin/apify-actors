"""Validated browserless client for public Pinterest search, pins, boards and profiles.

This module only reads information that Pinterest shows to logged-out visitors.
It never signs in, never sends a user's cookies, and skips private boards,
secret pins, home feeds and anything else that requires an account.

Validation (2026-09-14, local host, no proxy, no cookies):
* ``GET /resource/BaseSearchResource/get/`` returned HTTP 200 with
  ``resource_response.status == "success"``, 24-25 pins per default page and an
  opaque ``bookmark``.  Six consecutive pages produced 145 unique pin IDs with
  zero duplicates; ``page_size`` up to 250 is honoured (244 pins in one call).
  Scopes ``pins``, ``videos``, ``boards`` and ``users`` all work.
* ``PinResource`` (``field_set_key=unauth_react_main_pin``) returned the full
  closeup record including ``repin_count``, ``aggregated_pin_data.aggregated_stats.saves``,
  ``comment_count``, ``share_count``, ``hashtags`` and ``videos.video_list``.
* ``BoardResource`` + ``BoardFeedResource`` paginated a public board (three
  pages, 75 pins, no duplicates).  Board feed pins already include
  ``repin_count`` and aggregated saves, search pins do not.
* ``UserResource``, ``UserActivityPinsResource`` (created pins),
  ``UserPinsResource`` (saved pins) and ``BoardsResource`` all returned data.
* The only request header Pinterest insists on is ``X-Pinterest-PWS-Handler``;
  without it the API answers HTTP 403 ``Invalid Resource Request``.  CSRF
  cookies, ``X-APP-VERSION`` and ``X-Requested-With`` are optional.
* Thirty back-to-back search requests (one per 1.3 s) all succeeded, so there
  is no aggressive rate limit for modest volumes.  Unknown IDs return HTTP 404
  with ``resource_response.error.message`` such as ``Pin not found.``.

Pinterest's resource API is undocumented and may change without notice.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx


BASE_URL = 'https://www.pinterest.com'
RESOURCE_URL = BASE_URL + '/resource/{name}/get/'
DEFAULT_HEADERS = {
    'Accept': 'application/json, text/javascript, */*, q=0.01',
    'Accept-Language': 'en-US,en;q=0.9',
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36'
    ),
    'X-Requested-With': 'XMLHttpRequest',
    'X-Pinterest-AppState': 'active',
}
SEARCH_SCOPES = ('pins', 'videos', 'boards', 'users')
MAX_PAGE_SIZE = 250
END_BOOKMARK = '-end-'
# Profile tabs and site sections that must not be mistaken for a username.
RESERVED_PATHS = {
    'pin', 'search', 'ideas', 'today', 'explore', 'categories', 'settings', 'login', 'business',
    'about', 'careers', 'blog', 'help', 'policy', 'privacy', 'terms', 'oauth', 'resource', 'news_hub',
    'shopping', 'videos', 'pins', '_created', '_saved', '_shop', 'topics', 'discover', 'app', 'static',
}
USERNAME_PATTERN = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
PIN_ID_PATTERN = re.compile(r'^\d{6,25}$')


class PinterestError(RuntimeError):
    """Base error for this public Pinterest client."""


class PinterestNotFound(PinterestError):
    """Raised when Pinterest reports that a pin, board or user does not exist."""


class PinterestBlocked(PinterestError):
    """Raised when Pinterest returns a rate limit, block or challenge response."""


# --------------------------------------------------------------------------- URLs

def parse_pinterest_url(url: str) -> dict[str, Any]:
    """Classify a public Pinterest URL.

    Returns ``{'kind': 'pin', 'pinId': ...}``, ``{'kind': 'board', 'username', 'slug', 'boardUrl'}``,
    ``{'kind': 'user', 'username', 'tab'}`` or ``{'kind': 'search', 'query', 'scope'}``.
    ``pin.it`` short links are returned as ``{'kind': 'short', 'url': ...}`` so the caller can
    resolve them with :meth:`PinterestClient.resolve_short_url`.
    """
    raw = url.strip()
    if not raw:
        raise ValueError('Empty URL')
    if not re.match(r'^https?://', raw, re.I):
        raw = 'https://' + raw
    parts = urlsplit(raw)
    host = parts.hostname or ''
    if host == 'pin.it' or host.endswith('.pin.it'):
        return {'kind': 'short', 'url': raw}
    if 'pinterest.' not in host:
        raise ValueError(f'Not a Pinterest URL: {url}')
    segments = [unquote(segment) for segment in parts.path.split('/') if segment]
    if not segments:
        raise ValueError(f'Pinterest home page is not a scrapeable source: {url}')
    head = segments[0].lower()
    if head == 'pin' and len(segments) >= 2:
        pin_id = segments[1]
        if not PIN_ID_PATTERN.match(pin_id):
            raise ValueError(f'Unrecognised pin ID in URL: {url}')
        return {'kind': 'pin', 'pinId': pin_id}
    if head == 'search':
        query = parse_qs(parts.query).get('q', [''])[0].strip()
        if not query:
            raise ValueError(f'Search URL has no q= parameter: {url}')
        scope = segments[1].lower() if len(segments) >= 2 else 'pins'
        if scope not in SEARCH_SCOPES:
            scope = 'pins'
        return {'kind': 'search', 'query': query, 'scope': scope}
    if head in RESERVED_PATHS or not USERNAME_PATTERN.match(segments[0]):
        raise ValueError(f'Unsupported Pinterest URL: {url}')
    username = segments[0]
    if len(segments) == 1 or segments[1].lower() in {'_created', '_saved', 'pins', 'boards', '_shop'}:
        tab = segments[1].lower() if len(segments) > 1 else ''
        return {'kind': 'user', 'username': username, 'tab': tab}
    slug = segments[1]
    if slug.lower() in RESERVED_PATHS:
        raise ValueError(f'Unsupported Pinterest URL: {url}')
    return {'kind': 'board', 'username': username, 'slug': slug, 'boardUrl': f'/{username}/{slug}/'}


# ------------------------------------------------------------------- normalisers

def _iso(value: Any) -> str | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r'\s+', ' ', value).strip()
    return cleaned or None


def _user_summary(user: Any) -> dict[str, Any] | None:
    if not isinstance(user, dict) or not user.get('username'):
        return None
    return {
        'id': user.get('id'),
        'username': user.get('username'),
        'fullName': _text(user.get('full_name')),
        'followerCount': _int(user.get('follower_count')),
        'profileUrl': f"{BASE_URL}/{user['username']}/",
        'imageUrl': user.get('image_large_url') or user.get('image_medium_url'),
        'isVerifiedMerchant': bool(user.get('is_verified_merchant')),
    }


def _board_summary(board: Any) -> dict[str, Any] | None:
    if not isinstance(board, dict) or not board.get('id'):
        return None
    url = board.get('url')
    return {
        'id': board.get('id'),
        'name': _text(board.get('name')),
        'url': f'{BASE_URL}{url}' if isinstance(url, str) and url.startswith('/') else url,
        'pinCount': _int(board.get('pin_count')),
        'followerCount': _int(board.get('follower_count')),
        'privacy': board.get('privacy'),
    }


def _pick_video(videos: Any) -> dict[str, Any] | None:
    video_list = (videos or {}).get('video_list') if isinstance(videos, dict) else None
    if not isinstance(video_list, dict) or not video_list:
        return None
    preferred = None
    for key in ('V_720P', 'V_EXP7', 'V_EXP6', 'V_EXP5', 'V_EXP4', 'V_EXP3', 'V_HLSV4', 'V_HLSV3_MOBILE'):
        if key in video_list:
            preferred = video_list[key]
            break
    if preferred is None:
        preferred = next(iter(video_list.values()))
    mp4 = next((v.get('url') for v in video_list.values() if isinstance(v, dict) and str(v.get('url', '')).endswith('.mp4')), None)
    hls = next((v.get('url') for v in video_list.values() if isinstance(v, dict) and '.m3u8' in str(v.get('url', ''))), None)
    return {
        'url': mp4 or preferred.get('url'),
        'hlsUrl': hls,
        'width': _int(preferred.get('width')),
        'height': _int(preferred.get('height')),
        'durationMs': _int(preferred.get('duration')),
        'thumbnailUrl': preferred.get('thumbnail'),
    }


def normalize_pin(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten a raw pin object from any resource into a stable public record."""
    pin_id = str(raw.get('id') or '')
    images = raw.get('images') if isinstance(raw.get('images'), dict) else {}
    orig = images.get('orig') if isinstance(images.get('orig'), dict) else None
    largest = orig
    if largest is None and images:
        candidates = [v for v in images.values() if isinstance(v, dict) and v.get('url')]
        largest = max(candidates, key=lambda v: _int(v.get('width')) or 0, default=None)
    aggregated = raw.get('aggregated_pin_data') if isinstance(raw.get('aggregated_pin_data'), dict) else {}
    stats = aggregated.get('aggregated_stats') if isinstance(aggregated.get('aggregated_stats'), dict) else {}
    video = _pick_video(raw.get('videos'))
    story = raw.get('story_pin_data')
    rich = raw.get('rich_summary') if isinstance(raw.get('rich_summary'), dict) else None
    link = raw.get('link') or (rich or {}).get('url')
    domain = raw.get('domain') or raw.get('link_domain') or (rich or {}).get('site_name')
    description = _text(raw.get('description')) or _text(raw.get('closeup_unified_description')) or _text(raw.get('seo_description'))
    title = _text(raw.get('title')) or _text(raw.get('grid_title')) or _text((rich or {}).get('display_name'))
    pinner = _user_summary(raw.get('pinner')) or _user_summary(raw.get('native_creator'))
    board = _board_summary(raw.get('board'))
    return {
        'type': 'pin',
        'id': pin_id,
        'url': f'{BASE_URL}/pin/{pin_id}/',
        'title': title,
        'description': description,
        'altText': _text(raw.get('alt_text')) or _text(raw.get('seo_alt_text')) or _text(raw.get('auto_alt_text')),
        'link': link if isinstance(link, str) and link.startswith('http') else None,
        'domain': domain if domain and domain != 'Uploaded by user' else None,
        'isUploaded': domain == 'Uploaded by user' or bool(raw.get('is_uploaded')),
        'imageUrl': (largest or {}).get('url'),
        'imageWidth': _int((largest or {}).get('width')),
        'imageHeight': _int((largest or {}).get('height')),
        'imageUrl736': (images.get('736x') or {}).get('url') if isinstance(images.get('736x'), dict) else None,
        'imageUrl236': (images.get('236x') or {}).get('url') if isinstance(images.get('236x'), dict) else None,
        'dominantColor': raw.get('dominant_color'),
        'isVideo': bool(video) or bool(raw.get('is_video')),
        'video': video,
        'isIdeaPin': isinstance(story, dict) and bool(story),
        'ideaPinPageCount': _int((story or {}).get('page_count')) if isinstance(story, dict) else None,
        'isPromoted': bool(raw.get('is_promoted')),
        'isRepin': raw.get('is_repin') if isinstance(raw.get('is_repin'), bool) else None,
        'saveCount': _int(stats.get('saves')),
        'repinCount': _int(raw.get('repin_count')),
        'commentCount': _int(raw.get('comment_count')),
        'shareCount': _int(raw.get('share_count')),
        'reactionCounts': raw.get('reaction_counts') if isinstance(raw.get('reaction_counts'), dict) and raw.get('reaction_counts') else None,
        'hashtags': raw.get('hashtags') if isinstance(raw.get('hashtags'), list) else None,
        'createdAt': _iso(raw.get('created_at')),
        'pinner': pinner,
        'pinnerUsername': (pinner or {}).get('username'),
        'board': board,
        'boardName': (board or {}).get('name'),
        'boardUrl': (board or {}).get('url'),
        'productPrice': raw.get('price_value') if isinstance(raw.get('price_value'), (int, float)) and raw.get('price_value') > 0 else None,
        'productCurrency': raw.get('price_currency') if isinstance(raw.get('price_value'), (int, float)) and raw.get('price_value') > 0 else None,
    }


def normalize_board(raw: dict[str, Any]) -> dict[str, Any]:
    owner = _user_summary(raw.get('owner'))
    url = raw.get('url')
    cover = raw.get('image_cover_hd_url') or raw.get('image_cover_url')
    return {
        'type': 'board',
        'id': raw.get('id'),
        'url': f'{BASE_URL}{url}' if isinstance(url, str) and url.startswith('/') else url,
        'name': _text(raw.get('name')),
        'description': _text(raw.get('description')),
        'pinCount': _int(raw.get('pin_count')),
        'sectionCount': _int(raw.get('section_count')),
        'followerCount': _int(raw.get('follower_count')),
        'collaboratorCount': _int(raw.get('collaborator_count')),
        'privacy': raw.get('privacy'),
        'category': raw.get('category') or None,
        'coverImageUrl': cover,
        'createdAt': _iso(raw.get('created_at')),
        'lastPinnedAt': _iso(raw.get('board_order_modified_at')),
        'owner': owner,
        'ownerUsername': (owner or {}).get('username'),
    }


def normalize_user(raw: dict[str, Any]) -> dict[str, Any]:
    username = raw.get('username')
    return {
        'type': 'profile',
        'id': raw.get('id'),
        'url': f'{BASE_URL}/{username}/' if username else None,
        'username': username,
        'fullName': _text(raw.get('full_name')),
        'about': _text(raw.get('about')),
        'websiteUrl': raw.get('website_url') or raw.get('domain_url') or None,
        'imageUrl': raw.get('image_xlarge_url') or raw.get('image_large_url'),
        'pinCount': _int(raw.get('pin_count')),
        'boardCount': _int(raw.get('board_count')),
        'followerCount': _int(raw.get('follower_count')),
        'followingCount': _int(raw.get('following_count')),
        'videoPinCount': _int(raw.get('video_pin_count')),
        'isVerifiedMerchant': bool(raw.get('is_verified_merchant')),
        'isPartner': bool(raw.get('is_partner')),
        'createdAt': _iso(raw.get('created_at')),
        'lastPinSaveAt': _iso(raw.get('last_pin_save_time')),
    }


# ------------------------------------------------------------------------ client

class PinterestClient:
    """Small synchronous client with retry and optional proxy-session rotation."""

    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        proxy_url_factory: Callable[[], str | None] | None = None,
        max_retries: int = 3,
        min_delay: float = 0.4,
        rotate_every: int = 25,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.proxy_url = proxy_url
        self.proxy_url_factory = proxy_url_factory
        self.max_retries = max(1, max_retries)
        self.min_delay = max(0.0, min_delay)
        self.rotate_every = max(1, rotate_every)
        self.logger = logger or (lambda _message: None)
        self._request_count = 0
        self._client = self._build_client(proxy_url)

    @staticmethod
    def _build_client(proxy_url: str | None) -> httpx.Client:
        return httpx.Client(headers=DEFAULT_HEADERS, proxy=proxy_url, follow_redirects=True, timeout=httpx.Timeout(35))

    def close(self) -> None:
        self._client.close()

    def _rotate(self) -> None:
        if not self.proxy_url_factory:
            return
        proxy_url = self.proxy_url_factory()
        if not proxy_url:
            return
        self._client.close()
        self.proxy_url = proxy_url
        self._client = self._build_client(proxy_url)
        self._request_count = 0
        self.logger('Rotated Pinterest proxy session')

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            if self.proxy_url_factory and self._request_count >= self.rotate_every:
                self._rotate()
            if self._request_count:
                time.sleep(self.min_delay + random.uniform(0, self.min_delay / 2))
            try:
                response = self._client.request(method, url, **kwargs)
                self._request_count += 1
                if response.status_code == 404:
                    return response
                lowered = response.text[:3_000].lower()
                if response.status_code in {401, 403, 429, 503} or 'captcha' in lowered:
                    raise PinterestBlocked(f'Pinterest returned HTTP {response.status_code}: {response.text[:120]!r}')
                response.raise_for_status()
                return response
            except (httpx.HTTPError, PinterestBlocked) as exc:
                last_error = exc
                self.logger(f'Pinterest request attempt {attempt}/{self.max_retries} failed: {exc}')
                if attempt < self.max_retries:
                    self._rotate()
                    time.sleep(min(8, 2 ** (attempt - 1)))
        if isinstance(last_error, PinterestBlocked):
            raise last_error
        raise PinterestError(f'Pinterest request failed after {self.max_retries} attempts: {last_error}')

    def resource(self, name: str, options: dict[str, Any], *, source_url: str, handler: str) -> dict[str, Any]:
        """Call ``/resource/<name>/get/`` and return ``resource_response``."""
        params = {
            'source_url': source_url,
            'data': json.dumps({'options': options, 'context': {}}, separators=(',', ':')),
            '_': str(int(time.time() * 1000)),
        }
        headers = {
            'X-Pinterest-PWS-Handler': handler,
            'X-Pinterest-Source-Url': source_url,
            'Referer': BASE_URL + source_url,
        }
        response = self._request('GET', RESOURCE_URL.format(name=name), params=params, headers=headers)
        try:
            payload = response.json()
        except ValueError as exc:
            raise PinterestError(f'{name} returned non-JSON body (HTTP {response.status_code})') from exc
        resource_response = payload.get('resource_response') if isinstance(payload, dict) else None
        if not isinstance(resource_response, dict):
            raise PinterestError(f'{name} response had no resource_response')
        error = resource_response.get('error')
        if response.status_code == 404 or (isinstance(error, dict) and error.get('http_status') == 404):
            message = (error or {}).get('message') if isinstance(error, dict) else None
            raise PinterestNotFound(message or f'{name}: not found')
        if error or resource_response.get('status') not in (None, 'success'):
            raise PinterestError(f'{name} failed: {json.dumps(error or resource_response.get("status"))[:200]}')
        return resource_response

    # ---- search
    def search_page(self, query: str, *, scope: str = 'pins', page_size: int = 50, bookmark: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
        if scope not in SEARCH_SCOPES:
            raise ValueError(f'scope must be one of: {", ".join(SEARCH_SCOPES)}')
        query = query.strip()
        if not query:
            raise ValueError('query must not be empty')
        source_url = f'/search/{scope}/?q={query}&rs=typed'
        options = {
            'query': query,
            'scope': scope,
            'bookmarks': [bookmark] if bookmark else [],
            'page_size': max(1, min(MAX_PAGE_SIZE, page_size)),
            'rs': 'typed',
            'auto_correction_disabled': False,
            'redux_normalize_feed': True,
            'source_url': source_url,
        }
        rsp = self.resource('BaseSearchResource', options, source_url=source_url, handler='www/search/[scope].js')
        data = rsp.get('data')
        results = data.get('results') if isinstance(data, dict) else data
        next_bookmark = rsp.get('bookmark')
        if not next_bookmark or next_bookmark == END_BOOKMARK:
            next_bookmark = None
        return [item for item in (results or []) if isinstance(item, dict)], next_bookmark

    def iter_search(self, query: str, *, scope: str = 'pins', max_items: int = 100, page_size: int = 50) -> Iterator[dict[str, Any]]:
        """Yield raw result objects (pins, boards or users) for a search, de-duplicated by ID."""
        wanted = 'board' if scope == 'boards' else 'user' if scope == 'users' else 'pin'
        seen: set[str] = set()
        bookmark: str | None = None
        while len(seen) < max_items:
            results, bookmark = self.search_page(query, scope=scope, page_size=min(page_size, max_items - len(seen) + 5), bookmark=bookmark)
            fresh = 0
            for item in results:
                if item.get('type') != wanted or not item.get('id') or str(item['id']) in seen:
                    continue
                seen.add(str(item['id']))
                fresh += 1
                yield item
                if len(seen) >= max_items:
                    return
            if not bookmark or not fresh:
                return

    # ---- pins
    def get_pin(self, pin_id: str) -> dict[str, Any]:
        pin_id = str(pin_id).strip()
        if not PIN_ID_PATTERN.match(pin_id):
            raise ValueError('pin_id must be a numeric Pinterest pin ID')
        rsp = self.resource(
            'PinResource',
            {'id': pin_id, 'field_set_key': 'unauth_react_main_pin', 'noCache': True, 'fetch_visual_search_objects': False},
            source_url=f'/pin/{pin_id}/',
            handler='www/pin/[id].js',
        )
        data = rsp.get('data')
        if not isinstance(data, dict) or not data.get('id'):
            raise PinterestNotFound('Pin not found.')
        return data

    def resolve_short_url(self, url: str) -> str:
        response = self._request('GET', url)
        return str(response.url)

    # ---- boards
    def get_board(self, username: str, slug: str) -> dict[str, Any]:
        rsp = self.resource(
            'BoardResource',
            {'username': username, 'slug': slug, 'field_set_key': 'detailed'},
            source_url=f'/{username}/{slug}/',
            handler='www/[username]/[slug].js',
        )
        data = rsp.get('data')
        if not isinstance(data, dict) or not data.get('id'):
            raise PinterestNotFound('Board not found.')
        return data

    def iter_board_pins(self, board_id: str, board_url: str, *, max_items: int = 100, page_size: int = 50) -> Iterator[dict[str, Any]]:
        seen: set[str] = set()
        bookmark: str | None = None
        while len(seen) < max_items:
            options = {
                'board_id': str(board_id),
                'board_url': board_url,
                'currentFilter': -1,
                'field_set_key': 'react_grid_pin',
                'filter_section_pins': True,
                'sort': 'default',
                'layout': 'default',
                'page_size': max(1, min(MAX_PAGE_SIZE, page_size)),
                'redux_normalize_feed': True,
                'bookmarks': [bookmark] if bookmark else [],
            }
            rsp = self.resource('BoardFeedResource', options, source_url=board_url, handler='www/[username]/[slug].js')
            data = rsp.get('data') or []
            fresh = 0
            for item in data if isinstance(data, list) else []:
                if not isinstance(item, dict) or item.get('type') != 'pin' or not item.get('id') or str(item['id']) in seen:
                    continue
                seen.add(str(item['id']))
                fresh += 1
                yield item
                if len(seen) >= max_items:
                    return
            bookmark = rsp.get('bookmark')
            if not bookmark or bookmark == END_BOOKMARK or not fresh:
                return

    # ---- users
    def get_user(self, username: str) -> dict[str, Any]:
        rsp = self.resource(
            'UserResource',
            {'username': username, 'field_set_key': 'profile'},
            source_url=f'/{username}/',
            handler='www/[username].js',
        )
        data = rsp.get('data')
        if not isinstance(data, dict) or not data.get('id'):
            raise PinterestNotFound('User not found.')
        return data

    def iter_user_pins(self, username: str, *, kind: str = 'created', max_items: int = 100, page_size: int = 50) -> Iterator[dict[str, Any]]:
        """Yield a profile's public pins: ``kind='created'`` (their own pins) or ``'saved'``."""
        if kind == 'created':
            name, handler, source_url = 'UserActivityPinsResource', 'www/[username]/_created.js', f'/{username}/_created/'
            base = {'username': username, 'field_set_key': 'grid_item', 'is_own_profile_pins': False, 'exclude_add_pin_rep': True}
        elif kind == 'saved':
            name, handler, source_url = 'UserPinsResource', 'www/[username]/pins.js', f'/{username}/pins/'
            base = {'username': username, 'field_set_key': 'grid_item'}
        else:
            raise ValueError("kind must be 'created' or 'saved'")
        seen: set[str] = set()
        bookmark: str | None = None
        while len(seen) < max_items:
            options = dict(base, page_size=max(1, min(MAX_PAGE_SIZE, page_size)), redux_normalize_feed=True, bookmarks=[bookmark] if bookmark else [])
            rsp = self.resource(name, options, source_url=source_url, handler=handler)
            data = rsp.get('data') or []
            fresh = 0
            for item in data if isinstance(data, list) else []:
                if not isinstance(item, dict) or item.get('type') != 'pin' or not item.get('id') or str(item['id']) in seen:
                    continue
                seen.add(str(item['id']))
                fresh += 1
                yield item
                if len(seen) >= max_items:
                    return
            bookmark = rsp.get('bookmark')
            if not bookmark or bookmark == END_BOOKMARK or not fresh:
                return

    def iter_user_boards(self, username: str, *, max_items: int = 100, page_size: int = 50) -> Iterator[dict[str, Any]]:
        seen: set[str] = set()
        bookmark: str | None = None
        while len(seen) < max_items:
            options = {
                'username': username,
                'field_set_key': 'grid_item',
                'filter_stories': False,
                'sort': 'last_pinned_to',
                'page_size': max(1, min(MAX_PAGE_SIZE, page_size)),
                'bookmarks': [bookmark] if bookmark else [],
            }
            rsp = self.resource('BoardsResource', options, source_url=f'/{username}/_saved/', handler='www/[username]/_saved.js')
            data = rsp.get('data') or []
            fresh = 0
            for item in data if isinstance(data, list) else []:
                if not isinstance(item, dict) or item.get('type') != 'board' or not item.get('id') or str(item['id']) in seen:
                    continue
                seen.add(str(item['id']))
                fresh += 1
                yield item
                if len(seen) >= max_items:
                    return
            bookmark = rsp.get('bookmark')
            if not bookmark or bookmark == END_BOOKMARK or not fresh:
                return


# --------------------------------------------------------------------------- CLI

def _main() -> None:
    parser = argparse.ArgumentParser(description='Probe public Pinterest resources without a browser.')
    parser.add_argument('target', help='Search query, or a pin/board/profile URL')
    parser.add_argument('--max', type=int, default=10)
    parser.add_argument('--details', action='store_true', help='Fetch PinResource for each search pin')
    parser.add_argument('--proxy', default=None)
    args = parser.parse_args()
    client = PinterestClient(proxy_url=args.proxy, logger=print)
    try:
        if re.match(r'^https?://|^(www\.)?pinterest\.|^pin\.it/', args.target, re.I):
            info = parse_pinterest_url(args.target)
            if info['kind'] == 'short':
                info = parse_pinterest_url(client.resolve_short_url(info['url']))
            print('# parsed:', json.dumps(info))
            if info['kind'] == 'pin':
                print(json.dumps(normalize_pin(client.get_pin(info['pinId'])), indent=1, ensure_ascii=False))
            elif info['kind'] == 'board':
                board = client.get_board(info['username'], info['slug'])
                print(json.dumps(normalize_board(board), indent=1, ensure_ascii=False))
                for raw in client.iter_board_pins(board['id'], info['boardUrl'], max_items=args.max):
                    print(json.dumps(normalize_pin(raw), ensure_ascii=False)[:300])
            elif info['kind'] == 'user':
                print(json.dumps(normalize_user(client.get_user(info['username'])), indent=1, ensure_ascii=False))
                for raw in client.iter_user_pins(info['username'], max_items=args.max):
                    print(json.dumps(normalize_pin(raw), ensure_ascii=False)[:300])
            else:
                args.target = info['query']
        if not re.match(r'^https?://|^(www\.)?pinterest\.|^pin\.it/', args.target, re.I):
            started = time.time()
            count = 0
            for raw in client.iter_search(args.target, max_items=args.max):
                record = normalize_pin(raw)
                if args.details:
                    record = normalize_pin(client.get_pin(record['id']))
                count += 1
                print(json.dumps(record, ensure_ascii=False)[:300])
            print(f'# {count} pins in {time.time() - started:.1f}s')
    finally:
        client.close()


if __name__ == '__main__':
    _main()
