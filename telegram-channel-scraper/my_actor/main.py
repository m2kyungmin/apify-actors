"""Telegram Channel Scraper - scrapes public channel previews at https://t.me/s/<channel>."""

from __future__ import annotations

import asyncio
import random
import re
from datetime import datetime, timezone
from typing import Any

from apify import Actor
from bs4 import BeautifulSoup
from httpx import AsyncClient, HTTPError, Response

from .parser import min_post_id, parse_channel_info, parse_posts

USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
)
CHANNEL_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]{3,31}$')
MAX_RETRIES = 4
PAGE_SIZE_HINT = 20
EVENT_POST = 'post'
EVENT_CHANNEL = 'channel-info'


def normalize_channel(raw: str) -> str | None:
    """Accept `name`, `@name`, `t.me/name`, `https://t.me/s/name/123` and return `name`."""
    value = raw.strip()
    if not value:
        return None
    value = re.sub(r'^https?://', '', value, flags=re.IGNORECASE)
    value = re.sub(r'^(www\.)?(t\.me|telegram\.me|telegram\.dog)/', '', value, flags=re.IGNORECASE)
    value = re.sub(r'^s/', '', value, flags=re.IGNORECASE)
    value = value.split('?')[0].split('#')[0].strip('/').split('/')[0]
    value = value.lstrip('@')
    if value.startswith('+') or value.lower().startswith('joinchat'):
        return None  # invite links point at private chats
    return value if CHANNEL_RE.match(value) else None


def parse_date_bound(value: str | None, *, end_of_day: bool) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        if len(value) == 10:
            parsed = datetime.strptime(value, '%Y-%m-%d')
            if end_of_day:
                parsed = parsed.replace(hour=23, minute=59, second=59)
            return parsed.replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f'Invalid date "{value}". Use YYYY-MM-DD or ISO 8601.') from exc


class Budget:
    """Tracks pay-per-event charging so the run stops cleanly when the user's limit is hit."""

    def __init__(self) -> None:
        self.limit_reached = False

    async def charge(self, event: str, count: int) -> int:
        """Charge `count` events; returns how many were accepted (may be fewer at the limit)."""
        if count <= 0 or self.limit_reached:
            return 0
        try:
            manager = Actor.get_charging_manager()
            allowed = manager.calculate_max_event_charge_count_within_limit(event)
        except Exception:  # noqa: BLE001 - charging is best-effort outside PPE
            allowed = None
        to_charge = count if allowed is None else min(count, allowed)
        if to_charge <= 0:
            self.limit_reached = True
            return 0
        result = await Actor.charge(event, count=to_charge)
        if result.event_charge_limit_reached or to_charge < count:
            self.limit_reached = True
        return to_charge


async def fetch_page(client: AsyncClient, url: str) -> Response | None:
    """GET with retries on 429/5xx/network errors. Returns None if the channel does not exist."""
    delay = 1.5
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.get(url)
        except HTTPError as exc:
            Actor.log.warning(f'{url}: network error ({exc!r}), attempt {attempt}/{MAX_RETRIES}')
        else:
            if response.status_code == 200:
                return response
            if response.status_code in (301, 302, 303, 307, 308, 404):
                return None
            Actor.log.warning(f'{url}: HTTP {response.status_code}, attempt {attempt}/{MAX_RETRIES}')
            if response.status_code < 500 and response.status_code != 429:
                return None
        await asyncio.sleep(delay + random.random())
        delay *= 2
    raise RuntimeError(f'Giving up on {url} after {MAX_RETRIES} attempts')


async def scrape_channel(
    client: AsyncClient,
    channel: str,
    *,
    max_posts: int,
    newer_than: datetime | None,
    older_than: datetime | None,
    include_channel_info: bool,
    include_html: bool,
    budget: Budget,
) -> dict[str, int]:
    stats = {'posts': 0, 'pages': 0}
    base_url = f'https://t.me/s/{channel}'
    before: int | None = None
    seen_ids: set[int] = set()
    channel_pushed = False

    while stats['posts'] < max_posts and not budget.limit_reached:
        url = base_url if before is None else f'{base_url}?before={before}'
        response = await fetch_page(client, url)
        if response is None:
            if stats['pages'] == 0:
                Actor.log.error(f'@{channel}: channel not found, private, or has no public preview - skipping')
            break
        stats['pages'] += 1
        soup = BeautifulSoup(response.content, 'lxml')

        if stats['pages'] == 1:
            info = parse_channel_info(soup, channel)
            if info is None:
                Actor.log.error(f'@{channel}: page is not a channel preview (maybe a user or bot) - skipping')
                break
            Actor.log.info(
                f'@{channel}: "{info.get("title")}" - {info.get("subscribers")} subscribers'
            )
            if include_channel_info and not channel_pushed:
                charged = await budget.charge(EVENT_CHANNEL, 1)
                if charged:
                    await Actor.push_data(info)
                    channel_pushed = True

        page_posts = parse_posts(soup, channel, include_html=include_html)
        page_posts.sort(key=lambda p: p['postId'], reverse=True)  # newest first
        page_posts = [p for p in page_posts if p['postId'] not in seen_ids]
        if not page_posts:
            Actor.log.info(f'@{channel}: reached the beginning of channel history')
            break
        seen_ids.update(p['postId'] for p in page_posts)
        oldest_id = min_post_id(page_posts)

        selected: list[dict[str, Any]] = []
        stop_paging = False
        for post in page_posts:
            ts = post.get('timestamp')
            post_dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts is not None else None
            if older_than and post_dt and post_dt > older_than:
                continue
            if newer_than and post_dt and post_dt < newer_than:
                stop_paging = True
                break
            selected.append(post)
            if stats['posts'] + len(selected) >= max_posts:
                stop_paging = True
                break

        if selected:
            accepted = await budget.charge(EVENT_POST, len(selected))
            selected = selected[:accepted]
            if selected:
                await Actor.push_data(selected)
                stats['posts'] += len(selected)

        Actor.log.info(f'@{channel}: page {stats["pages"]} -> {len(selected)} posts (total {stats["posts"]})')

        if stop_paging or oldest_id is None or oldest_id <= 1:
            break
        before = oldest_id
        await asyncio.sleep(0.4 + random.random() * 0.6)

    return stats


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}

        raw_channels = actor_input.get('channels') or []
        if isinstance(raw_channels, str):
            raw_channels = [raw_channels]
        channels: list[str] = []
        for raw in raw_channels:
            name = normalize_channel(str(raw))
            if name is None:
                Actor.log.warning(f'Ignoring invalid channel "{raw}" (private invite links are not supported)')
            elif name.lower() not in {c.lower() for c in channels}:
                channels.append(name)
        if not channels:
            raise ValueError('Input "channels" must contain at least one public channel username or t.me URL.')

        max_posts = int(actor_input.get('maxPostsPerChannel') or 50)
        newer_than = parse_date_bound(actor_input.get('postsNewerThan'), end_of_day=False)
        older_than = parse_date_bound(actor_input.get('postsOlderThan'), end_of_day=True)
        include_channel_info = bool(actor_input.get('includeChannelInfo', True))
        include_html = bool(actor_input.get('includeTextHtml', False))

        proxy_url = None
        proxy_input = actor_input.get('proxyConfiguration') or {}
        if proxy_input.get('useApifyProxy') or proxy_input.get('proxyUrls'):
            proxy_config = await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
            if proxy_config:
                proxy_url = await proxy_config.new_url()
                Actor.log.info('Using proxy for requests')

        budget = Budget()
        Actor.log.info(f'Scraping {len(channels)} channel(s), up to {max_posts} posts each')

        totals = {'posts': 0, 'pages': 0, 'channelsOk': 0, 'channelsFailed': 0}
        async with AsyncClient(
            headers={'User-Agent': USER_AGENT, 'Accept-Language': 'en-US,en;q=0.9'},
            timeout=30,
            follow_redirects=False,
            proxy=proxy_url,
        ) as client:
            for channel in channels:
                if budget.limit_reached:
                    Actor.log.warning('Maximum charge limit reached - stopping before remaining channels')
                    break
                try:
                    stats = await scrape_channel(
                        client,
                        channel,
                        max_posts=max_posts,
                        newer_than=newer_than,
                        older_than=older_than,
                        include_channel_info=include_channel_info,
                        include_html=include_html,
                        budget=budget,
                    )
                except Exception as exc:  # noqa: BLE001 - one bad channel must not kill the run
                    Actor.log.exception(f'@{channel}: failed ({exc})')
                    totals['channelsFailed'] += 1
                    continue
                totals['posts'] += stats['posts']
                totals['pages'] += stats['pages']
                if stats['pages'] > 0:
                    totals['channelsOk'] += 1
                else:
                    totals['channelsFailed'] += 1

        Actor.log.info(
            f'Done: {totals["posts"]} posts from {totals["channelsOk"]} channel(s), '
            f'{totals["channelsFailed"]} channel(s) failed, {totals["pages"]} pages fetched'
        )
        await Actor.set_value('SUMMARY', totals)
        if totals['channelsOk'] == 0:
            raise RuntimeError('No channel could be scraped. Check that the channels are public and spelled correctly.')
