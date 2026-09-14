"""Validated browserless client for public Google Play app pages and reviews.

This module reads only publicly visible Google Play information.  It does not
sign in, send cookies from a user, solve challenges, or access developer
console data.  Review records intentionally omit reviewer display names and
avatars; those fields are not needed to analyse an app's public feedback.

Validation (2026-09-14):
* ``GET /store/apps/details`` returned HTTP 200 and a parseable public HTML
  page for ``com.instagram.android``.
* ``POST /_/PlayStoreUi/data/batchexecute`` with RPC ``oCPfdb`` returned three
  review records and a continuation token.  Supplying that token returned a
  second three-record page with no duplicate review IDs.

Google's web RPC is undocumented and may change.  Consumers should keep the
page size modest, use an Apify proxy for production Google traffic, and treat
an RPC parse failure as an item failure rather than charging for it.
"""

from __future__ import annotations

import argparse
import html as html_module
import json
import random
import re
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx


DETAIL_URL = 'https://play.google.com/store/apps/details'
BATCHEXECUTE_URL = 'https://play.google.com/_/PlayStoreUi/data/batchexecute'
REVIEWS_RPC = 'oCPfdb'

DEFAULT_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36'
    ),
}
RPC_HEADERS = {
    'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
    'X-Requested-With': 'XMLHttpRequest',
}
SORTS = {'mostRelevant': 1, 'newest': 2, 'rating': 3}
APP_ID_PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$')
EMAIL_PATTERN = re.compile(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', re.I)
PHONE_PATTERN = re.compile(r'(?<!\w)(?:\+?\d[\d(). -]{6,}\d)(?!\w)')


class GooglePlayError(RuntimeError):
    """Base error for this public Google Play client."""


class GooglePlayBlocked(GooglePlayError):
    """Raised when Google returns a rate limit, block, or challenge page."""


def _clean_text(value: str) -> str:
    return re.sub(r'\s+', ' ', html_module.unescape(re.sub(r'<[^>]+>', ' ', value))).strip()


def _attr(tag: str, name: str) -> str | None:
    match = re.search(r'\b' + re.escape(name) + r'''\s*=\s*(["'])(.*?)\1''', tag, re.I | re.S)
    return html_module.unescape(match.group(2)).strip() if match else None


def _meta(html: str, name: str) -> str | None:
    for tag in re.findall(r'<meta\b[^>]*>', html, re.I):
        if _attr(tag, 'name') == name or _attr(tag, 'property') == name:
            return _attr(tag, 'content')
    return None


def _first_tag_attribute(html: str, *, tag_name: str, attribute: str, expected: str) -> str | None:
    for tag in re.findall(r'<' + re.escape(tag_name) + r'\b[^>]*>', html, re.I):
        if _attr(tag, attribute) == expected:
            return _attr(tag, 'src')
    return None


def _find_first(pattern: str, html: str) -> str | None:
    match = re.search(pattern, html, re.I | re.S)
    return _clean_text(match.group(1)) if match else None


def _public_text(value: str | None) -> str | None:
    """Keep review text useful while redacting obvious accidental contact data."""
    if not value:
        return None
    value = EMAIL_PATTERN.sub('[redacted email]', value)
    value = PHONE_PATTERN.sub('[redacted phone]', value)
    return value


def parse_app_html(page_html: str, *, app_id: str, language: str, country: str) -> dict[str, Any]:
    """Parse stable, visible fields from one public app-detail HTML response."""
    title = _find_first(r'<h1\b[^>]*>\s*(.*?)\s*</h1>', page_html)
    if not title:
        title = _meta(page_html, 'og:title')
        if title:
            title = re.sub(r'\s+-\s+Apps on Google Play\s*$', '', title).strip()
    if not title:
        raise GooglePlayError('Public app title was not found; page layout may have changed')

    developer_url = _find_first(r'<a\b[^>]*href=["\']([^"\']*/store/apps/developer\?[^"\']*)["\'][^>]*>', page_html)
    developer = _find_first(
        r'<a\b[^>]*href=["\'][^"\']*/store/apps/developer\?[^"\']*["\'][^>]*>\s*(.*?)\s*</a>',
        page_html,
    )
    developer_id = None
    if developer_url:
        developer_id = parse_qs(urlsplit(developer_url).query).get('id', [None])[0]

    rating_match = re.search(r'aria-label=["\']Rated\s+([\d.,]+)\s+stars', page_html, re.I)
    rating = float(rating_match.group(1).replace(',', '.')) if rating_match else None
    review_count = _find_first(r'<div\b[^>]*class=["\'][^"\']*g1rdde[^"\']*["\'][^>]*>\s*([^<]+?)\s+reviews\s*</div>', page_html)
    downloads = _find_first(
        r'<div\b[^>]*>\s*([^<]+?)\s*</div>\s*<div\b[^>]*class=["\'][^"\']*g1rdde[^"\']*["\'][^>]*>\s*Downloads\s*</div>',
        page_html,
    )
    content_rating = _find_first(r'<span\b[^>]*itemprop=["\']contentRating["\'][^>]*>\s*<span[^>]*>\s*(.*?)\s*</span>', page_html)
    icon = _first_tag_attribute(page_html, tag_name='img', attribute='alt', expected='Icon image')
    summary = _meta(page_html, 'og:description') or _meta(page_html, 'description')
    description = _meta(page_html, 'description') or summary
    bundle_id = _meta(page_html, 'appstore:bundle_id')

    screenshots: list[str] = []
    for tag in re.findall(r'<img\b[^>]*>', page_html, re.I):
        alt = _attr(tag, 'alt') or ''
        src = _attr(tag, 'src')
        if src and alt.lower().startswith('screenshot') and src not in screenshots:
            screenshots.append(src)

    return {
        'appId': bundle_id or app_id,
        'url': f'{DETAIL_URL}?{urlencode({"id": app_id, "hl": language, "gl": country})}',
        'title': title,
        'summary': summary,
        'description': description,
        'developer': developer,
        'developerId': developer_id,
        'developerUrl': f'https://play.google.com{developer_url}' if developer_url else None,
        'developerWebsite': _meta(page_html, 'appstore:developer_url'),
        'iconUrl': icon or _meta(page_html, 'og:image'),
        'rating': rating,
        'reviewCountText': review_count,
        'downloadsText': downloads,
        'contentRating': content_rating,
        'containsAds': bool(re.search(r'>\s*Contains ads\s*<', page_html, re.I)),
        'screenshots': screenshots,
        'language': language,
        'country': country,
    }


def _nested(value: Any, *indexes: int) -> Any:
    try:
        for index in indexes:
            value = value[index]
        return value
    except (IndexError, KeyError, TypeError):
        return None


def _parse_batchexecute(text: str) -> Any:
    """Extract the ``oCPfdb`` payload from Google's XSSI-protected envelope."""
    try:
        envelope = json.loads(text.split('\n\n', 1)[1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise GooglePlayError('Unexpected batchexecute envelope') from exc
    for entry in envelope:
        if len(entry) >= 3 and entry[0] == 'wrb.fr' and entry[1] == REVIEWS_RPC:
            try:
                return json.loads(entry[2])
            except (TypeError, json.JSONDecodeError) as exc:
                raise GooglePlayError('Review payload was not JSON') from exc
    raise GooglePlayError(f'batchexecute did not include {REVIEWS_RPC}')


def _parse_review(row: list[Any]) -> dict[str, Any] | None:
    review_id = _nested(row, 0)
    score = _nested(row, 2)
    if not isinstance(review_id, str) or not isinstance(score, int):
        return None
    timestamp = _nested(row, 5, 0)
    created_at = None
    if isinstance(timestamp, (int, float)):
        created_at = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    reply_timestamp = _nested(row, 7, 2, 0)
    replied_at = None
    if isinstance(reply_timestamp, (int, float)):
        replied_at = datetime.fromtimestamp(reply_timestamp, tz=timezone.utc).isoformat()
    return {
        'reviewId': review_id,
        'score': score,
        'content': _public_text(_nested(row, 4)),
        'createdAt': created_at,
        'thumbsUpCount': _nested(row, 6),
        'appVersion': _nested(row, 10),
        'developerReply': _public_text(_nested(row, 7, 1)),
        'developerRepliedAt': replied_at,
    }


class GooglePlayClient:
    """Small synchronous client with retry and optional proxy-session rotation."""

    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        proxy_url_factory: Callable[[], str | None] | None = None,
        max_retries: int = 3,
        min_delay: float = 0.6,
        rotate_every: int = 12,
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
        return httpx.Client(
            headers=DEFAULT_HEADERS,
            proxy=proxy_url,
            follow_redirects=True,
            timeout=httpx.Timeout(35),
        )

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
        self.logger('Rotated Google Play proxy session')

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
                lowered = response.text[:4_000].lower()
                if response.status_code in {403, 429, 503} or 'unusual traffic' in lowered or 'recaptcha' in lowered:
                    raise GooglePlayBlocked(f'Google Play returned HTTP {response.status_code} or a challenge page')
                response.raise_for_status()
                return response
            except (httpx.HTTPError, GooglePlayBlocked) as exc:
                last_error = exc
                self.logger(f'Google Play request attempt {attempt}/{self.max_retries} failed: {exc}')
                if attempt < self.max_retries:
                    self._rotate()
                    time.sleep(min(8, 2 ** (attempt - 1)))
        if isinstance(last_error, GooglePlayBlocked):
            raise last_error
        raise GooglePlayError(f'Google Play request failed after {self.max_retries} attempts: {last_error}')

    @staticmethod
    def _validate_app_id(app_id: str) -> str:
        app_id = app_id.strip()
        if not APP_ID_PATTERN.fullmatch(app_id):
            raise ValueError('app_id must be an Android package ID, such as com.example.app')
        return app_id

    def get_app(self, app_id: str, *, language: str = 'en', country: str = 'US') -> dict[str, Any]:
        app_id = self._validate_app_id(app_id)
        response = self._request('GET', DETAIL_URL, params={'id': app_id, 'hl': language, 'gl': country})
        return parse_app_html(response.text, app_id=app_id, language=language, country=country)

    @staticmethod
    def _review_body(
        app_id: str,
        *,
        sort: int,
        page_size: int,
        continuation_token: str | None,
        score: int | None,
    ) -> str:
        token_part: list[Any] = [page_size] if continuation_token is None else [page_size, None, continuation_token]
        filters: list[Any] = [None, score, None, None, None, None, None, None, None]
        inner = [None, [2, sort, token_part, None, filters], [app_id, 7]]
        outer = [[[REVIEWS_RPC, json.dumps(inner, separators=(',', ':')), None, 'generic']]]
        return json.dumps(outer, separators=(',', ':'))

    def reviews_page(
        self,
        app_id: str,
        *,
        language: str = 'en',
        country: str = 'US',
        sort: str = 'newest',
        page_size: int = 100,
        continuation_token: str | None = None,
        score: int | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Return one review page and its opaque token, without reviewer identity fields."""
        app_id = self._validate_app_id(app_id)
        if sort not in SORTS:
            raise ValueError(f'sort must be one of: {", ".join(SORTS)}')
        if not 1 <= page_size <= 200:
            raise ValueError('page_size must be between 1 and 200')
        if score is not None and score not in {1, 2, 3, 4, 5}:
            raise ValueError('score must be one of 1, 2, 3, 4, 5')
        response = self._request(
            'POST',
            BATCHEXECUTE_URL,
            params={'hl': language, 'gl': country},
            data={'f.req': self._review_body(app_id, sort=SORTS[sort], page_size=page_size, continuation_token=continuation_token, score=score)},
            headers=RPC_HEADERS,
        )
        payload = _parse_batchexecute(response.text)
        rows = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], list) else []
        token = _nested(payload, -2, -1) if isinstance(payload, list) else None
        return [record for row in rows if (record := _parse_review(row))], token if isinstance(token, str) else None

    def iter_reviews(
        self,
        app_id: str,
        *,
        max_reviews: int,
        language: str = 'en',
        country: str = 'US',
        sort: str = 'newest',
        score: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield at most ``max_reviews`` public review records across pages."""
        if max_reviews < 1:
            return
        token: str | None = None
        remaining = max_reviews
        seen_ids: set[str] = set()
        while remaining > 0:
            page, token = self.reviews_page(
                app_id,
                language=language,
                country=country,
                sort=sort,
                page_size=min(100, remaining),
                continuation_token=token,
                score=score,
            )
            for review in page:
                if review['reviewId'] in seen_ids:
                    continue
                seen_ids.add(review['reviewId'])
                yield review
                remaining -= 1
                if remaining == 0:
                    return
            if not token or not page:
                return

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GooglePlayClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def main() -> None:
    parser = argparse.ArgumentParser(description='Read public Google Play app metadata and a small review sample.')
    parser.add_argument('app_id', nargs='?', default='com.instagram.android')
    parser.add_argument('--reviews', type=int, default=3, help='Maximum public reviews to print (default: 3)')
    parser.add_argument('--language', default='en')
    parser.add_argument('--country', default='US')
    parser.add_argument('--proxy', help='Optional proxy URL; pass only through a secure runtime configuration')
    args = parser.parse_args()
    with GooglePlayClient(proxy_url=args.proxy, logger=print) as client:
        app = client.get_app(args.app_id, language=args.language, country=args.country)
        review_sample = list(client.iter_reviews(
            args.app_id,
            max_reviews=max(0, args.reviews),
            language=args.language,
            country=args.country,
        ))
    print(json.dumps({'app': app, 'reviewSample': review_sample}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
