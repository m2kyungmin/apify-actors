"""Apify Actor entry point for public Threads profiles, posts, replies and search."""

from __future__ import annotations

import asyncio
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from apify import Actor

from .threads_client import (
    PROFILE_POSTS_DOC_ID,
    ThreadsBlocked,
    ThreadsClient,
    ThreadsError,
    ThreadsNotFound,
    ThreadsSchemaChanged,
    normalize_post,
    normalize_user,
    parse_threads_url,
)

EVENT_POST = 'post'
EVENT_PROFILE = 'profile'
EVENTS = (EVENT_POST, EVENT_PROFILE)


class Budget:
    """Keep each pay-per-event charge inside the run's configured spending limit."""

    def __init__(self) -> None:
        self.limits: dict[str, bool] = {event: False for event in EVENTS}

    def available(self, event_name: str, requested: int) -> int:
        try:
            allowed = Actor.get_charging_manager().calculate_max_event_charge_count_within_limit(event_name)
        except Exception:  # noqa: BLE001
            allowed = None
        return requested if allowed is None else min(requested, max(0, allowed))

    async def charge_one(self, event_name: str) -> bool:
        if self.limits[event_name] or self.available(event_name, 1) < 1:
            self.limits[event_name] = True
            return False
        charge = await Actor.charge(event_name, count=1)
        self.limits[event_name] = charge.event_charge_limit_reached
        return True


def _strings(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in values:
        if isinstance(item, dict):
            item = item.get('url')
        text = str(item or '').strip()
        if text and text not in out:
            out.append(text)
    return out


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        profiles = _strings(inp.get('profiles'))
        post_urls = _strings(inp.get('postUrls'))
        queries = _strings(inp.get('searchQueries'))
        if not profiles and not post_urls and not queries:
            raise ValueError('Provide at least one profile, post URL or search query.')
        max_posts = max(1, min(2000, int(inp.get('maxPostsPerProfile') or 50)))
        include_replies = bool(inp.get('includeReplies', True))
        doc_id = str(inp.get('profilePostsDocId') or PROFILE_POSTS_DOC_ID).strip() or PROFILE_POSTS_DOC_ID
        budget = Budget()

        proxy_input = inp.get('proxyConfiguration') or {'useApifyProxy': True}
        proxy_url = None
        proxy_factory = None
        if proxy_input.get('useApifyProxy') or proxy_input.get('proxyUrls'):
            proxy_config = await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
            if proxy_config:
                seed_url = await proxy_config.new_url(session_id=f'threads_{secrets.token_hex(4)}')
                proxy_url = seed_url

                def _next_proxy_url() -> str:
                    # Only swap the session ID; keep the password, host and other parameters.
                    return re.sub(r'session-[^,:@]+', f'session-threads_{secrets.token_hex(4)}', seed_url, count=1)

                proxy_factory = _next_proxy_url
                Actor.log.info('Using Apify Proxy and rotating the session every 15 Threads requests')

        client = ThreadsClient(
            proxy_url=proxy_url,
            proxy_url_factory=proxy_factory,
            profile_posts_doc_id=doc_id,
            logger=lambda message: Actor.log.debug(message),
        )
        totals = {'posts': 0, 'profiles': 0, 'failedSources': 0, 'sources': len(profiles) + len(post_urls) + len(queries)}
        seen_posts: set[str] = set()

        async def push_post(raw: dict[str, Any], source: str, *, extra: dict[str, Any] | None = None) -> bool:
            record = normalize_post(raw)
            if not record['id'] or record['id'] in seen_posts:
                return True
            if not await budget.charge_one(EVENT_POST):
                Actor.log.warning('The post-event spending limit was reached; stopping')
                return False
            seen_posts.add(record['id'])
            record.update(extra or {})
            record['source'] = source
            record['scrapedAt'] = _now()
            await Actor.push_data(record)
            totals['posts'] += 1
            return True

        async def push_profile(raw: dict[str, Any], source: str) -> bool:
            record = normalize_user(raw)
            if not await budget.charge_one(EVENT_PROFILE):
                Actor.log.warning('The profile-event spending limit was reached; stopping')
                return False
            record['source'] = source
            record['scrapedAt'] = _now()
            await Actor.push_data(record)
            totals['profiles'] += 1
            return True

        async def run_profile(value: str) -> bool:
            try:
                info = parse_threads_url(value)
                if info['kind'] != 'profile':
                    raise ValueError('expected a profile @handle or profile URL')
                username = info['username']
                user, first_posts, cursor, session = await asyncio.to_thread(client.get_profile, username)
            except (ThreadsError, ValueError) as exc:
                totals['failedSources'] += 1
                Actor.log.warning(f'Profile {value!r} failed and was not charged: {exc}')
                return True
            source = f'profile:@{username}'
            if not await push_profile(user, source):
                return False
            allowed = budget.available(EVENT_POST, max_posts)
            if allowed < 1:
                return False
            collected: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for post in first_posts:
                if post['pk'] not in seen_ids and len(collected) < allowed:
                    seen_ids.add(post['pk'])
                    collected.append(post)
            try:
                while cursor and len(collected) < allowed:
                    page, cursor = await asyncio.to_thread(
                        client.profile_posts_page, user['pk'], cursor, session, username=username
                    )
                    fresh = 0
                    for post in page:
                        if post['pk'] in seen_ids:
                            continue
                        seen_ids.add(post['pk'])
                        collected.append(post)
                        fresh += 1
                        if len(collected) >= allowed:
                            break
                    if not fresh:
                        break
            except ThreadsSchemaChanged as exc:
                Actor.log.warning(f'Profile @{username}: pagination stopped early ({exc}). Delivering the {len(collected)} posts already fetched.')
            except ThreadsError as exc:
                Actor.log.warning(f'Profile @{username}: pagination stopped early ({exc}).')
            Actor.log.info(f'Profile @{username}: {len(collected)} posts')
            for post in collected:
                if not await push_post(post, source):
                    return False
            return True

        async def run_post(value: str) -> bool:
            try:
                info = parse_threads_url(value)
                if info['kind'] != 'post':
                    raise ValueError('expected a post URL like https://www.threads.com/@user/post/CODE')
                post, replies = await asyncio.to_thread(client.get_post, info['username'], info['code'])
            except (ThreadsError, ValueError) as exc:
                totals['failedSources'] += 1
                Actor.log.warning(f'Post {value!r} failed and was not charged: {exc}')
                return True
            source = f'post:{value}'
            if not await push_post(post, source):
                return False
            if include_replies:
                Actor.log.info(f'Post {info["code"]}: {len(replies)} replies')
                for reply in replies:
                    if not await push_post(reply, source, extra={'isReply': True, 'replyToPostCode': info['code'], 'replyToUsername': info['username']}):
                        return False
            return True

        async def run_search(query: str) -> bool:
            try:
                posts = await asyncio.to_thread(client.search_posts, query)
            except (ThreadsError, ValueError) as exc:
                totals['failedSources'] += 1
                Actor.log.warning(f'Search {query!r} failed and was not charged: {exc}')
                return True
            Actor.log.info(f'Search {query!r}: {len(posts)} posts')
            for post in posts:
                if not await push_post(post, f'search:{query}'):
                    return False
            return True

        try:
            ok = True
            for value in profiles:
                ok = await run_profile(value)
                if not ok:
                    break
            if ok:
                for value in post_urls:
                    ok = await run_post(value)
                    if not ok:
                        break
            if ok:
                for query in queries:
                    ok = await run_search(query)
                    if not ok:
                        break
        except ThreadsBlocked as exc:
            Actor.log.error(f'Threads blocked the requests: {exc}')
        finally:
            client.close()

        await Actor.set_value('SUMMARY', totals)
        Actor.log.info(f'Finished: {totals}')
        if not (totals['posts'] or totals['profiles']):
            raise RuntimeError('No public Threads data was collected. Check the handles/URLs or retry with Apify Proxy enabled.')


if __name__ == '__main__':
    asyncio.run(main())
