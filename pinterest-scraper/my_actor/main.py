"""Apify Actor entry point for public Pinterest search, pins, boards and profiles."""

from __future__ import annotations

import asyncio
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from apify import Actor

from .pinterest_client import (
    PinterestBlocked,
    PinterestClient,
    PinterestError,
    PinterestNotFound,
    normalize_board,
    normalize_pin,
    normalize_user,
    parse_pinterest_url,
)

EVENT_PIN = 'pin'
EVENT_BOARD = 'board'
EVENT_PROFILE = 'profile'
EVENTS = (EVENT_PIN, EVENT_BOARD, EVENT_PROFILE)


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
        queries = _strings(inp.get('searchQueries'))
        start_urls = _strings(inp.get('startUrls'))
        if not queries and not start_urls:
            raise ValueError('Provide at least one search query in searchQueries or one Pinterest URL in startUrls.')

        scope = str(inp.get('searchScope') or 'pins')
        if scope not in ('pins', 'videos', 'boards', 'users'):
            scope = 'pins'
        max_per_source = max(1, min(5000, int(inp.get('maxPinsPerSource') or 50)))
        profile_pins = str(inp.get('profilePins') or 'created')
        fetch_details = bool(inp.get('fetchPinDetails', True))
        budget = Budget()

        proxy_input = inp.get('proxyConfiguration') or {'useApifyProxy': True}
        proxy_url = None
        proxy_factory = None
        if proxy_input.get('useApifyProxy') or proxy_input.get('proxyUrls'):
            proxy_config = await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
            if proxy_config:
                seed_url = await proxy_config.new_url(session_id=f'pinterest_{secrets.token_hex(4)}')
                proxy_url = seed_url

                # Pinterest requests run in a worker thread.  Deriving a fresh
                # session URL from the seed avoids awaiting new_url() in that
                # thread while still changing the Apify Proxy session/IP.
                def _next_proxy_url() -> str:
                    # Only swap the session ID; keep the password, host and any
                    # other username parameters (for example country-XX).
                    return re.sub(r'session-[^,:@]+', f'session-pinterest_{secrets.token_hex(4)}', seed_url, count=1)

                proxy_factory = _next_proxy_url
                Actor.log.info('Using Apify Proxy and rotating the session every 25 Pinterest requests')

        client = PinterestClient(
            proxy_url=proxy_url,
            proxy_url_factory=proxy_factory,
            rotate_every=25,
            logger=lambda message: Actor.log.debug(message),
        )
        totals = {'pins': 0, 'boards': 0, 'profiles': 0, 'failedSources': 0, 'failedPins': 0, 'sources': len(queries) + len(start_urls)}
        seen_pins: set[str] = set()

        async def push_pin(raw: dict[str, Any], source: str, *, enrich: bool) -> bool:
            """Normalise, optionally enrich, charge and store one pin. Returns False when the budget is exhausted."""
            record = normalize_pin(raw)
            if not record['id'] or record['id'] in seen_pins:
                return True
            if enrich and record['saveCount'] is None:
                try:
                    record = normalize_pin(await asyncio.to_thread(client.get_pin, record['id']))
                except PinterestNotFound:
                    pass
                except PinterestError as exc:
                    Actor.log.warning(f'Pin {record["id"]} detail fetch failed, keeping the list record: {exc}')
            if not await budget.charge_one(EVENT_PIN):
                Actor.log.warning('The pin-event spending limit was reached; stopping')
                return False
            seen_pins.add(record['id'])
            record['source'] = source
            record['scrapedAt'] = _now()
            await Actor.push_data(record)
            totals['pins'] += 1
            return True

        async def push_board(raw: dict[str, Any], source: str) -> bool:
            record = normalize_board(raw)
            if not await budget.charge_one(EVENT_BOARD):
                Actor.log.warning('The board-event spending limit was reached; stopping')
                return False
            record['source'] = source
            record['scrapedAt'] = _now()
            await Actor.push_data(record)
            totals['boards'] += 1
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

        async def run_search(query: str, search_scope: str) -> bool:
            source = f'search:{search_scope}:{query}'
            event = EVENT_BOARD if search_scope == 'boards' else EVENT_PROFILE if search_scope == 'users' else EVENT_PIN
            allowed = budget.available(event, max_per_source)
            if allowed < 1:
                return False
            try:
                results = await asyncio.to_thread(
                    lambda: list(client.iter_search(query, scope=search_scope, max_items=allowed))
                )
            except (PinterestError, ValueError) as exc:
                totals['failedSources'] += 1
                Actor.log.warning(f'Search {query!r} ({search_scope}) failed and was not charged: {exc}')
                return True
            Actor.log.info(f'Search {query!r} ({search_scope}): {len(results)} results')
            for raw in results:
                if search_scope == 'boards':
                    ok = await push_board(raw, source)
                elif search_scope == 'users':
                    ok = await push_profile(raw, source)
                else:
                    ok = await push_pin(raw, source, enrich=fetch_details)
                if not ok:
                    return False
            return True

        async def run_url(url: str) -> bool:
            try:
                info = parse_pinterest_url(url)
                if info['kind'] == 'short':
                    info = parse_pinterest_url(await asyncio.to_thread(client.resolve_short_url, info['url']))
            except (ValueError, PinterestError) as exc:
                totals['failedSources'] += 1
                Actor.log.warning(f'URL {url!r} skipped: {exc}')
                return True

            if info['kind'] == 'search':
                return await run_search(info['query'], info['scope'])

            if info['kind'] == 'pin':
                if budget.available(EVENT_PIN, 1) < 1:
                    return False
                try:
                    raw = await asyncio.to_thread(client.get_pin, info['pinId'])
                except (PinterestError, ValueError) as exc:
                    totals['failedPins'] += 1
                    Actor.log.warning(f'Pin {url!r} failed and was not charged: {exc}')
                    return True
                return await push_pin(raw, f'pin:{url}', enrich=False)

            if info['kind'] == 'board':
                try:
                    board = await asyncio.to_thread(client.get_board, info['username'], info['slug'])
                except (PinterestError, ValueError) as exc:
                    totals['failedSources'] += 1
                    Actor.log.warning(f'Board {url!r} failed and was not charged: {exc}')
                    return True
                source = f'board:{url}'
                if not await push_board(board, source):
                    return False
                allowed = budget.available(EVENT_PIN, max_per_source)
                if allowed < 1:
                    return False
                try:
                    pins = await asyncio.to_thread(
                        lambda: list(client.iter_board_pins(board['id'], info['boardUrl'], max_items=allowed))
                    )
                except PinterestError as exc:
                    totals['failedSources'] += 1
                    Actor.log.warning(f'Board feed for {url!r} failed and was not charged: {exc}')
                    return True
                Actor.log.info(f'Board {url}: {len(pins)} pins')
                for raw in pins:
                    if not await push_pin(raw, source, enrich=False):
                        return False
                return True

            # profile
            try:
                user = await asyncio.to_thread(client.get_user, info['username'])
            except (PinterestError, ValueError) as exc:
                totals['failedSources'] += 1
                Actor.log.warning(f'Profile {url!r} failed and was not charged: {exc}')
                return True
            source = f'profile:{url}'
            if not await push_profile(user, source):
                return False
            kind = {'_created': 'created', 'pins': 'saved', '_saved': 'saved'}.get(info.get('tab', ''), profile_pins)
            if kind == 'none':
                return True
            allowed = budget.available(EVENT_PIN, max_per_source)
            if allowed < 1:
                return False
            try:
                pins = await asyncio.to_thread(
                    lambda: list(client.iter_user_pins(info['username'], kind=kind, max_items=allowed))
                )
            except PinterestError as exc:
                totals['failedSources'] += 1
                Actor.log.warning(f'Profile pins for {url!r} failed and were not charged: {exc}')
                return True
            Actor.log.info(f'Profile {url} ({kind}): {len(pins)} pins')
            for raw in pins:
                if not await push_pin(raw, source, enrich=False):
                    return False
            return True

        try:
            for query in queries:
                if not await run_search(query, scope):
                    break
            else:
                for url in start_urls:
                    if not await run_url(url):
                        break
        except PinterestBlocked as exc:
            Actor.log.error(f'Pinterest blocked the requests: {exc}')
        finally:
            client.close()

        await Actor.set_value('SUMMARY', totals)
        Actor.log.info(f'Finished: {totals}')
        if not (totals['pins'] or totals['boards'] or totals['profiles']):
            raise RuntimeError('No public Pinterest data was collected. Check the queries/URLs or retry with Apify Proxy enabled.')


if __name__ == '__main__':
    asyncio.run(main())
