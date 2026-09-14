"""Validated browserless client for public (logged-out) Threads profiles, posts and search.

Reads only what threads.com shows to a visitor without an account.  No login,
no user cookies, no private data.

Validation (2026-09-15, local host, no proxy):
* ``GET https://www.threads.com/@zuck`` returns the full 970 KB page **only when
  the request carries browser navigation headers** (``Sec-Fetch-Mode: navigate``,
  ``Sec-Fetch-Dest: document``, ``Sec-Fetch-Site: none``,
  ``Upgrade-Insecure-Requests: 1``); without them Threads serves a 270 KB empty
  shell.  The page embeds Relay payloads in ``<script type="application/json"
  data-sjs>`` under ``RelayPrefetchedStreamCache``: the profile
  (``BarcelonaProfilePageDirectQuery`` -> ``user``) and the first posts
  (``BarcelonaProfileThreadsTabDirectQuery`` -> ``mediaData.edges`` +
  ``page_info.end_cursor``).
* Pagination: ``POST /graphql/query`` with
  ``fb_api_req_friendly_name=BarcelonaProfileThreadsTabRefetchableDirectQuery``,
  ``doc_id`` (see ``PROFILE_POSTS_DOC_ID``), the session values read from the
  page (``lsd``, ``__hs``, ``__rev``, ``__hsi``), header
  ``X-Logged-Out-Threads-Migrated-Request: true`` and the exact logged-out
  ``__relay_internal__pv__*`` provider variables captured from a logged-out
  headless browser.  Three pages returned 53 unique posts.  The logged-in
  variant of the same query (different provider values) returns
  ``execution error`` for anonymous callers.
* Post page ``/@user/post/<code>``: ``BarcelonaPostPageDirectQuery`` ->
  ``data.edges``: edge 0 is the post itself, the remaining edges are reply
  threads (20 on the sample).
* Search page ``/search?q=<query>&serp_type=default`` embeds
  ``BarcelonaSearchResultsQuery`` -> ``searchResults.edges`` with ~20 posts.

The GraphQL ``doc_id`` and provider variables change when Meta ships a new web
build; keep ``PROFILE_POSTS_DOC_ID`` and ``LOGGED_OUT_PROVIDERS`` updated.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

import httpx


BASE_URL = 'https://www.threads.com'
GRAPHQL_URL = BASE_URL + '/graphql/query'
IG_APP_ID = '238260118697367'
ASBD_ID = '359341'
BLOKS_VERSION_ID = '03cc45721a9d9734bea72949f1cb37f26f2b977d75732de17ea4de385592f106'
PROFILE_POSTS_DOC_ID = '28150103917987977'
PROFILE_POSTS_QUERY = 'BarcelonaProfileThreadsTabRefetchableDirectQuery'
PROFILE_POSTS_ROOT_FIELD = 'xdt_api__v1__text_feed__user_id__profile__connection'
# Captured 2026-09-15 from a logged-out headless Chromium session.
LOGGED_OUT_PROVIDERS: dict[str, bool] = {
    '__relay_internal__pv__BarcelonaIsLoggedInrelayprovider': False,
    '__relay_internal__pv__BarcelonaHasProfileSelfReplyContextrelayprovider': False,
    '__relay_internal__pv__BarcelonaHasDearAlgoConsumptionrelayprovider': True,
    '__relay_internal__pv__BarcelonaHasMetaAiContentAttachmentsrelayprovider': False,
    '__relay_internal__pv__BarcelonaHasEventBadgerelayprovider': False,
    '__relay_internal__pv__BarcelonaGenAIRepliesEnabledrelayprovider': False,
    '__relay_internal__pv__BarcelonaIsSearchDiscoveryEnabledrelayprovider': False,
    '__relay_internal__pv__BarcelonaHasCommunitiesrelayprovider': True,
    '__relay_internal__pv__BarcelonaHasGameScoreSharerelayprovider': True,
    '__relay_internal__pv__BarcelonaMessagesHasLiveChatMessagingrelayprovider': False,
    '__relay_internal__pv__BarcelonaHasPublicViewCountCardrelayprovider': True,
    '__relay_internal__pv__BarcelonaHasCommunityEmojiUpdateCardrelayprovider': True,
    '__relay_internal__pv__BarcelonaHasCommunityEntityCardrelayprovider': False,
    '__relay_internal__pv__BarcelonaHasScorecardCommunityrelayprovider': False,
    '__relay_internal__pv__BarcelonaHasSportTeamAllegianceCardrelayprovider': False,
    '__relay_internal__pv__BarcelonaHasMusicrelayprovider': True,
    '__relay_internal__pv__BarcelonaHasNewspaperLinkStylerelayprovider': False,
    '__relay_internal__pv__BarcelonaHasMessagingrelayprovider': False,
    '__relay_internal__pv__BarcelonaHasPodcastV2Consumptionrelayprovider': True,
    '__relay_internal__pv__BarcelonaHasPodcastTranscriptConsumptionrelayprovider': True,
    '__relay_internal__pv__BarcelonaShouldFulfillLightboxQueryrelayprovider': True,
    '__relay_internal__pv__BarcelonaHasViewerRepliedrelayprovider': True,
    '__relay_internal__pv__BarcelonaHasPrivateRepliesDeprecationrelayprovider': False,
    '__relay_internal__pv__BarcelonaHasGhostPostEmojiActivationrelayprovider': False,
    '__relay_internal__pv__BarcelonaOptionalCookiesEnabledrelayprovider': True,
    '__relay_internal__pv__BarcelonaHasDearAlgoWebProductionrelayprovider': False,
    '__relay_internal__pv__BarcelonaHasWebFaviconsrelayprovider': False,
    '__relay_internal__pv__BarcelonaIsCrawlerrelayprovider': False,
    '__relay_internal__pv__BarcelonaHasCommunityTopContributorsrelayprovider': False,
    '__relay_internal__pv__BarcelonaCanSeeSponsoredContentrelayprovider': False,
    '__relay_internal__pv__BarcelonaShouldShowFediverseM075Featuresrelayprovider': False,
    '__relay_internal__pv__BarcelonaIsInternalUserrelayprovider': False,
}
PAGE_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Site': 'none',
    'Upgrade-Insecure-Requests': '1',
}
USERNAME_PATTERN = re.compile(r'^[A-Za-z0-9._]{1,30}$')
POST_CODE_PATTERN = re.compile(r'^[A-Za-z0-9_-]{6,20}$')
MEDIA_TYPES = {1: 'image', 2: 'video', 8: 'carousel', 19: 'text'}


class ThreadsError(RuntimeError):
    """Base error for this public Threads client."""


class ThreadsNotFound(ThreadsError):
    """Profile or post does not exist or is not public."""


class ThreadsBlocked(ThreadsError):
    """Threads served a login wall, rate limit or an empty shell instead of data."""


class ThreadsSchemaChanged(ThreadsError):
    """The GraphQL query id / provider variables no longer match Threads' web build."""


# --------------------------------------------------------------------------- URLs

def parse_threads_url(value: str) -> dict[str, Any]:
    """Classify a Threads URL or ``@handle``: ``{'kind': 'profile'|'post'|'search', ...}``."""
    raw = value.strip()
    if not raw:
        raise ValueError('Empty input')
    if raw.startswith('@'):
        username = raw[1:]
        if not USERNAME_PATTERN.match(username):
            raise ValueError(f'Invalid Threads handle: {value}')
        return {'kind': 'profile', 'username': username}
    if not re.match(r'^https?://', raw, re.I):
        if not re.match(r'^(www\.)?threads\.(com|net)/', raw, re.I):
            raise ValueError(f'Not a Threads URL or @handle: {value}')
        raw = 'https://' + raw
    parts = urlsplit(raw)
    host = (parts.hostname or '').lower()
    if not (host.endswith('threads.com') or host.endswith('threads.net')):
        raise ValueError(f'Not a Threads URL: {value}')
    segments = [s for s in parts.path.split('/') if s]
    if not segments:
        raise ValueError(f'Threads home page is not a scrapeable source: {value}')
    if segments[0].lower() == 'search':
        query = ''
        for pair in parts.query.split('&'):
            if pair.startswith('q='):
                query = httpx.URL(raw).params.get('q', '')
        if not query:
            raise ValueError(f'Search URL has no q= parameter: {value}')
        return {'kind': 'search', 'query': query}
    if not segments[0].startswith('@'):
        raise ValueError(f'Unsupported Threads URL: {value}')
    username = segments[0][1:]
    if not USERNAME_PATTERN.match(username):
        raise ValueError(f'Invalid Threads handle in URL: {value}')
    if len(segments) >= 3 and segments[1].lower() == 'post':
        code = segments[2]
        if not POST_CODE_PATTERN.match(code):
            raise ValueError(f'Invalid post code in URL: {value}')
        return {'kind': 'post', 'username': username, 'code': code}
    return {'kind': 'profile', 'username': username}


# ------------------------------------------------------------------- normalisers

def _iso(ts: Any) -> str | None:
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _best_candidate(container: Any) -> dict[str, Any] | None:
    cands = (container or {}).get('candidates') if isinstance(container, dict) else None
    if not isinstance(cands, list) or not cands:
        return None
    return max((c for c in cands if isinstance(c, dict) and c.get('url')), key=lambda c: _int(c.get('width')) or 0, default=None)


def _media(post: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    images: list[dict[str, Any]] = []
    videos: list[dict[str, Any]] = []

    def one(item: dict[str, Any]) -> None:
        vv = item.get('video_versions')
        if isinstance(vv, list) and vv:
            best = max((v for v in vv if isinstance(v, dict) and v.get('url')), key=lambda v: _int(v.get('width')) or 0, default=None)
            if best:
                thumb = _best_candidate(item.get('image_versions2'))
                videos.append({'url': best['url'], 'width': _int(best.get('width')), 'height': _int(best.get('height')),
                               'thumbnailUrl': (thumb or {}).get('url')})
                return
        img = _best_candidate(item.get('image_versions2'))
        if img:
            images.append({'url': img['url'], 'width': _int(img.get('width')), 'height': _int(img.get('height'))})

    carousel = post.get('carousel_media')
    if isinstance(carousel, list) and carousel:
        for item in carousel:
            if isinstance(item, dict):
                one(item)
    else:
        one(post)
    return images, videos


def _user_summary(user: Any) -> dict[str, Any] | None:
    if not isinstance(user, dict) or not user.get('username'):
        return None
    return {
        'id': user.get('pk') or user.get('id'),
        'username': user['username'],
        'fullName': user.get('full_name'),
        'isVerified': bool(user.get('is_verified')),
        'profilePicUrl': user.get('profile_pic_url'),
        'profileUrl': f"{BASE_URL}/@{user['username']}",
    }


def normalize_post(raw: dict[str, Any]) -> dict[str, Any]:
    info = raw.get('text_post_app_info') if isinstance(raw.get('text_post_app_info'), dict) else {}
    author = _user_summary(raw.get('user'))
    code = raw.get('code')
    images, videos = _media(raw)
    link = info.get('link_preview_attachment') if isinstance(info.get('link_preview_attachment'), dict) else None
    reply_to = info.get('reply_to_author') if isinstance(info.get('reply_to_author'), dict) else None
    media_type = MEDIA_TYPES.get(raw.get('media_type'), str(raw.get('media_type')) if raw.get('media_type') is not None else None)
    if media_type == 'text' and (images or videos):
        media_type = 'video' if videos else 'image'
    return {
        'type': 'post',
        'id': raw.get('pk'),
        'code': code,
        'url': f"{BASE_URL}/@{author['username']}/post/{code}" if author and code else None,
        'text': ((raw.get('caption') or {}).get('text') if isinstance(raw.get('caption'), dict) else None),
        'createdAt': _iso(raw.get('taken_at')),
        'likeCount': _int(raw.get('like_count')),
        'replyCount': _int(info.get('direct_reply_count')),
        'repostCount': _int(info.get('repost_count')),
        'quoteCount': _int(info.get('quote_count')),
        'reshareCount': _int(info.get('reshare_count')),
        'mediaType': media_type,
        'images': images,
        'videos': videos,
        'linkPreview': {'url': link.get('url'), 'displayUrl': link.get('display_url'), 'title': link.get('title'),
                        'imageUrl': link.get('image_url')} if link else None,
        'isReply': bool(info.get('is_reply')),
        'replyToUsername': reply_to.get('username') if reply_to else None,
        'language': raw.get('detected_language') or None,
        'isPaidPartnership': bool(raw.get('is_paid_partnership')),
        'author': author,
        'authorUsername': (author or {}).get('username'),
    }


def normalize_user(raw: dict[str, Any]) -> dict[str, Any]:
    links = raw.get('bio_links') if isinstance(raw.get('bio_links'), list) else []
    hd = raw.get('hd_profile_pic_versions') if isinstance(raw.get('hd_profile_pic_versions'), list) else []
    hd_best = max((v for v in hd if isinstance(v, dict) and v.get('url')), key=lambda v: _int(v.get('width')) or 0, default=None)
    return {
        'type': 'profile',
        'id': raw.get('pk') or raw.get('id'),
        'username': raw.get('username'),
        'url': f"{BASE_URL}/@{raw.get('username')}",
        'fullName': raw.get('full_name'),
        'bio': raw.get('biography'),
        'bioLinks': [l.get('url') for l in links if isinstance(l, dict) and l.get('url')],
        'followerCount': _int(raw.get('follower_count')),
        'isVerified': bool(raw.get('is_verified')),
        'isPrivate': bool(raw.get('text_post_app_is_private')),
        'profilePicUrl': (hd_best or {}).get('url') or raw.get('profile_pic_url'),
    }


# ------------------------------------------------------------------- page parsing

def extract_relay_payloads(html: str) -> dict[str, Any]:
    """Return ``{preloader_name: data}`` for every RelayPrefetchedStreamCache entry in the page."""
    out: dict[str, Any] = {}
    for script in re.findall(r'<script type="application/json"[^>]*data-sjs[^>]*>(.*?)</script>', html, re.S):
        try:
            doc = json.loads(script)
        except ValueError:
            continue

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == 'require' and isinstance(value, list):
                        for item in value:
                            if isinstance(item, list) and item and item[0] == 'RelayPrefetchedStreamCache' and len(item) > 3:
                                args = item[3]
                                if isinstance(args, list) and len(args) >= 2 and isinstance(args[1], dict):
                                    result = (args[1].get('__bbox') or {}).get('result') or {}
                                    out[str(args[0])] = result.get('data')
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(doc)
    return out


def _find_payload(payloads: dict[str, Any], marker: str) -> Any:
    for name, data in payloads.items():
        if marker in name:
            return data
    return None


def _threads_from_edges(edges: Any) -> Iterator[dict[str, Any]]:
    for edge in edges or []:
        node = edge.get('node') if isinstance(edge, dict) else None
        for item in (node or {}).get('thread_items') or []:
            post = item.get('post') if isinstance(item, dict) else None
            if isinstance(post, dict) and post.get('pk'):
                yield post


class PageSession:
    """Values scraped from one HTML page that the GraphQL endpoint needs."""

    def __init__(self, html: str, cookies: httpx.Cookies) -> None:
        def grab(pattern: str) -> str | None:
            match = re.search(pattern, html)
            return match.group(1) if match else None

        self.lsd = grab(r'"LSD",\[\],\{"token":"([^"]+)"')
        self.haste_session = grab(r'"haste_session":"([^"]+)"')
        self.server_revision = grab(r'"server_revision":(\d+)')
        self.hsi = grab(r'"hsi":"(\d+)"')
        self.csrf = cookies.get('csrftoken')
        if not self.lsd:
            raise ThreadsBlocked('Threads page did not include an LSD token (login wall or empty shell)')


# ------------------------------------------------------------------------ client

class ThreadsClient:
    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        proxy_url_factory: Callable[[], str | None] | None = None,
        max_retries: int = 3,
        min_delay: float = 0.5,
        rotate_every: int = 15,
        profile_posts_doc_id: str = PROFILE_POSTS_DOC_ID,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.proxy_url = proxy_url
        self.proxy_url_factory = proxy_url_factory
        self.max_retries = max(1, max_retries)
        self.min_delay = max(0.0, min_delay)
        self.rotate_every = max(1, rotate_every)
        self.profile_posts_doc_id = profile_posts_doc_id
        self.logger = logger or (lambda _m: None)
        self._request_count = 0
        self._client = self._build(proxy_url)

    @staticmethod
    def _build(proxy_url: str | None) -> httpx.Client:
        return httpx.Client(headers=PAGE_HEADERS, proxy=proxy_url, follow_redirects=True, timeout=httpx.Timeout(35))

    def close(self) -> None:
        self._client.close()

    def _rotate(self) -> None:
        if not self.proxy_url_factory:
            return
        url = self.proxy_url_factory()
        if not url:
            return
        self._client.close()
        self.proxy_url = url
        self._client = self._build(url)
        self._request_count = 0
        self.logger('Rotated Threads proxy session')

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last: Exception | None = None
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
                if response.status_code in {401, 403, 429, 503}:
                    raise ThreadsBlocked(f'Threads returned HTTP {response.status_code}')
                response.raise_for_status()
                return response
            except (httpx.HTTPError, ThreadsBlocked) as exc:
                last = exc
                self.logger(f'Threads request attempt {attempt}/{self.max_retries} failed: {exc}')
                if attempt < self.max_retries:
                    self._rotate()
                    time.sleep(min(8, 2 ** (attempt - 1)))
        if isinstance(last, ThreadsBlocked):
            raise last
        raise ThreadsError(f'Threads request failed after {self.max_retries} attempts: {last}')

    # ---- pages
    def fetch_page(self, path: str) -> tuple[str, dict[str, Any], PageSession]:
        last_error: ThreadsBlocked | None = None
        for attempt in range(1, 4):
            response = self._request('GET', BASE_URL + path)
            if response.status_code == 404:
                raise ThreadsNotFound(f'{path}: not found')
            html = response.text
            payloads = extract_relay_payloads(html)
            if payloads:
                return html, payloads, PageSession(html, self._client.cookies)
            title = re.search(r'<title>(.*?)</title>', html, re.S)
            title_text = (title.group(1) if title else '').strip()
            # A missing / non-public profile or post is served as the generic "Threads • Log in" shell.
            if 'Log in' in title_text or re.search(r"page isn.{0,6}t available|Page not found", html, re.I):
                raise ThreadsNotFound(f'{path}: not found or not available to logged-out visitors')
            # A data-less shell with a normal title is Threads throttling a busy IP: rotate and retry.
            last_error = ThreadsBlocked(f'{path}: Threads served an empty shell ({len(html)} bytes) instead of page data')
            self.logger(f'{path}: empty shell on attempt {attempt}/3, rotating proxy session and retrying')
            self._rotate()
            time.sleep(2 * attempt)
        raise last_error  # type: ignore[misc]

    def get_profile(self, username: str) -> tuple[dict[str, Any], list[dict[str, Any]], str | None, PageSession]:
        """Return (raw user, raw first posts, end_cursor, session) for a public profile."""
        username = username.lstrip('@')
        if not USERNAME_PATTERN.match(username):
            raise ValueError('username must be a Threads handle')
        _html, payloads, session = self.fetch_page(f'/@{username}')
        user = (_find_payload(payloads, 'BarcelonaProfilePageDirectQuery') or {}).get('user')
        if not isinstance(user, dict) or not user.get('pk'):
            raise ThreadsNotFound(f'@{username}: profile not found or not public')
        tab = _find_payload(payloads, 'BarcelonaProfileThreadsTabDirectQuery') or {}
        media = tab.get('mediaData') if isinstance(tab, dict) else None
        posts = list(_threads_from_edges((media or {}).get('edges')))
        page_info = (media or {}).get('page_info') or {}
        cursor = page_info.get('end_cursor') if page_info.get('has_next_page') else None
        return user, posts, cursor, session

    def profile_posts_page(self, user_id: str, cursor: str, session: PageSession, *, username: str, first: int = 11) -> tuple[list[dict[str, Any]], str | None]:
        variables = {
            'after': cursor, 'allow_page_info_for_lox_user': True, 'before': None, 'first': first, 'last': None,
            'userID': str(user_id), **LOGGED_OUT_PROVIDERS,
        }
        jazoest = '2' + str(sum(ord(ch) for ch in session.lsd))
        data = {
            'av': '0', '__user': '0', '__a': '1', '__req': 'a', '__hs': session.haste_session or '', 'dpr': '2',
            '__ccg': 'EXCELLENT', '__rev': session.server_revision or '', '__hsi': session.hsi or '', '__comet_req': '122',
            'lsd': session.lsd, 'jazoest': jazoest, '__spin_r': session.server_revision or '', '__spin_b': 'trunk',
            '__spin_t': str(int(time.time())), '__crn': 'comet.barcelonawebloggedout.BarcelonaProfileThreadsColumnRoute',
            'fb_api_caller_class': 'RelayModern', 'fb_api_req_friendly_name': PROFILE_POSTS_QUERY,
            'server_timestamps': 'true', 'variables': json.dumps(variables, separators=(',', ':')),
            'doc_id': self.profile_posts_doc_id,
        }
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded', 'Accept': '*/*',
            'X-FB-LSD': session.lsd, 'X-IG-App-ID': IG_APP_ID, 'X-ASBD-ID': ASBD_ID, 'X-CSRFToken': session.csrf or '',
            'X-FB-Friendly-Name': PROFILE_POSTS_QUERY, 'X-Root-Field-Name': PROFILE_POSTS_ROOT_FIELD,
            'X-Logged-Out-Threads-Migrated-Request': 'true', 'X-Bloks-Version-Id': BLOKS_VERSION_ID,
            'Origin': BASE_URL, 'Referer': f'{BASE_URL}/@{username}',
            'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'empty',
        }
        response = self._request('POST', GRAPHQL_URL, data=data, headers=headers)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ThreadsError('profile pagination returned non-JSON') from exc
        data_node = payload.get('data') if isinstance(payload, dict) else None
        if not data_node:
            errors = payload.get('errors') if isinstance(payload, dict) else None
            message = (errors or [{}])[0].get('message') if errors else 'no data'
            raise ThreadsSchemaChanged(f'profile pagination failed ({message}); the GraphQL doc_id/provider variables may be outdated')
        connection = data_node.get(PROFILE_POSTS_ROOT_FIELD) or next(iter(data_node.values()), None) or {}
        posts = list(_threads_from_edges(connection.get('edges')))
        page_info = connection.get('page_info') or {}
        next_cursor = page_info.get('end_cursor') if page_info.get('has_next_page') else None
        return posts, next_cursor

    def iter_profile_posts(self, username: str, *, max_posts: int = 50) -> Iterator[dict[str, Any]]:
        """Yield raw posts of a profile, newest first, paginating as needed (profile record is not yielded)."""
        user, posts, cursor, session = self.get_profile(username)
        seen: set[str] = set()
        for post in posts:
            if post['pk'] not in seen:
                seen.add(post['pk'])
                yield post
                if len(seen) >= max_posts:
                    return
        while cursor and len(seen) < max_posts:
            page, cursor = self.profile_posts_page(user['pk'], cursor, session, username=username.lstrip('@'))
            fresh = 0
            for post in page:
                if post['pk'] in seen:
                    continue
                seen.add(post['pk'])
                fresh += 1
                yield post
                if len(seen) >= max_posts:
                    return
            if not fresh:
                return

    # ---- posts
    def get_post(self, username: str, code: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return (raw post, raw replies) for a public post page."""
        username = username.lstrip('@')
        _html, payloads, _session = self.fetch_page(f'/@{username}/post/{code}')
        page = _find_payload(payloads, 'BarcelonaPostPageDirectQuery') or {}
        edges = ((page.get('data') or {}) if isinstance(page, dict) else {}).get('edges') or []
        if not edges:
            raise ThreadsNotFound(f'@{username}/post/{code}: post not found or not public')
        main_items = list(_threads_from_edges(edges[:1]))
        target = next((p for p in main_items if p.get('code') == code), main_items[0] if main_items else None)
        if target is None:
            raise ThreadsNotFound(f'@{username}/post/{code}: post not found')
        replies = [p for p in _threads_from_edges(edges[1:]) if p.get('pk') != target.get('pk')]
        return target, replies

    # ---- search
    def search_posts(self, query: str) -> list[dict[str, Any]]:
        """Return the raw posts on the first (server-rendered) search results page."""
        query = query.strip()
        if not query:
            raise ValueError('query must not be empty')
        _html, payloads, _session = self.fetch_page(f'/search?q={quote(query)}&serp_type=default')
        results = _find_payload(payloads, 'BarcelonaSearchResultsQuery') or {}
        edges = (results.get('searchResults') or {}).get('edges') if isinstance(results, dict) else None
        posts: list[dict[str, Any]] = []
        for edge in edges or []:
            node = edge.get('node') if isinstance(edge, dict) else None
            thread = (node or {}).get('thread') or node or {}
            for item in thread.get('thread_items') or []:
                post = item.get('post') if isinstance(item, dict) else None
                if isinstance(post, dict) and post.get('pk'):
                    posts.append(post)
        return posts


# --------------------------------------------------------------------------- CLI

def _main() -> None:
    parser = argparse.ArgumentParser(description='Probe public Threads profiles/posts/search without a browser.')
    parser.add_argument('target', help='@handle, profile URL, post URL, or a search query')
    parser.add_argument('--max', type=int, default=15)
    parser.add_argument('--proxy', default=None)
    args = parser.parse_args()
    client = ThreadsClient(proxy_url=args.proxy, logger=print)
    started = time.time()
    try:
        try:
            info = parse_threads_url(args.target)
        except ValueError:
            info = {'kind': 'search', 'query': args.target}
        print('# parsed:', json.dumps(info))
        if info['kind'] == 'profile':
            user, _posts, _cursor, _s = client.get_profile(info['username'])
            print(json.dumps(normalize_user(user), indent=1, ensure_ascii=False))
            n = 0
            for raw in client.iter_profile_posts(info['username'], max_posts=args.max):
                n += 1
                print(json.dumps(normalize_post(raw), ensure_ascii=False)[:220])
            print(f'# {n} posts')
        elif info['kind'] == 'post':
            post, replies = client.get_post(info['username'], info['code'])
            print(json.dumps(normalize_post(post), indent=1, ensure_ascii=False))
            print(f'# {len(replies)} replies; first:', json.dumps(normalize_post(replies[0]), ensure_ascii=False)[:200] if replies else None)
        else:
            posts = client.search_posts(info['query'])
            for raw in posts[: args.max]:
                print(json.dumps(normalize_post(raw), ensure_ascii=False)[:220])
            print(f'# {len(posts)} search posts')
        print(f'# {time.time() - started:.1f}s')
    finally:
        client.close()


if __name__ == '__main__':
    _main()
