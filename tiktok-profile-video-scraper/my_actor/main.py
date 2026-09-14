"""Apify Actor entry point for public TikTok profile and video pages."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from apify import Actor

from .tiktok_client import TikTokBlocked, TikTokClient, TikTokError, normalize_username, normalize_video_url

EVENT_RESULT = 'result'


class Budget:
    def __init__(self) -> None:
        self.limit_reached = False

    def available(self, requested: int) -> int:
        try:
            allowed = Actor.get_charging_manager().calculate_max_event_charge_count_within_limit(EVENT_RESULT)
        except Exception:  # noqa: BLE001
            allowed = None
        return requested if allowed is None else min(requested, max(0, allowed))

    async def charge_one(self) -> bool:
        if self.limit_reached or self.available(1) < 1:
            self.limit_reached = True
            return False
        charged = await Actor.charge(EVENT_RESULT, count=1)
        self.limit_reached = charged.event_charge_limit_reached
        return True


def _strings(value: Any) -> list[str]:
    if not value:
        return []
    values = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in values if str(item).strip()]


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        usernames = [normalize_username(value) for value in _strings(inp.get('usernames'))]
        video_urls = [normalize_video_url(value) for value in _strings(inp.get('videoUrls'))]
        if not usernames and not video_urls:
            raise ValueError('Provide at least one username or public TikTok video URL.')

        requested = max(1, min(100, int(inp.get('maxItems') or 20)))
        budget = Budget()
        available = budget.available(requested)
        if available <= 0:
            await Actor.set_value('SUMMARY', {'results': 0, 'failed': 0, 'requested': requested})
            return

        proxy_config = None
        proxy_input = inp.get('proxyConfiguration') or {}
        if proxy_input.get('useApifyProxy') or proxy_input.get('proxyUrls'):
            proxy_config = await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
            Actor.log.info('Using a fresh proxy session per public page request')

        planned = [('profile', username) for username in usernames] + [('video', url) for url in video_urls]
        totals = {'results': 0, 'failed': 0, 'requested': requested, 'profiles': 0, 'videos': 0}
        for kind, value in planned:
            if totals['results'] >= available or budget.limit_reached:
                break
            proxy_url = None
            if proxy_config:
                proxy_url = await proxy_config.new_url(session_id=f'tiktok_{secrets.token_hex(4)}')
            client = TikTokClient(proxy_url=proxy_url)
            try:
                item = await (client.get_profile(value) if kind == 'profile' else client.get_video(value))
            except TikTokBlocked as exc:
                totals['failed'] += 1
                Actor.log.warning(f'{kind} {value!r} was blocked and was not charged: {exc}')
                continue
            except TikTokError as exc:
                totals['failed'] += 1
                Actor.log.warning(f'{kind} {value!r} could not be parsed and was not charged: {exc}')
                continue

            item['scrapedAt'] = datetime.now(timezone.utc).isoformat()
            if not await budget.charge_one():
                break
            await Actor.push_data(item)
            totals['results'] += 1
            totals[f'{kind}s'] += 1
            Actor.log.info(f'Collected {kind} {value!r} ({totals["results"]}/{available})')

        await Actor.set_value('SUMMARY', totals)
        if totals['results'] == 0:
            raise RuntimeError('No public TikTok data was collected. Retry later or enable a proxy if TikTok served a WAF challenge.')
