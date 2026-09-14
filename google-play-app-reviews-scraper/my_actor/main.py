"""Apify Actor entry point for public Google Play app details and reviews."""

from __future__ import annotations

import asyncio
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from apify import Actor

from .google_play_client import GooglePlayBlocked, GooglePlayClient, GooglePlayError

EVENT_APP = 'app'
EVENT_REVIEW = 'review'


class Budget:
    """Keep each pay-per-event charge inside the run's configured spending limit."""

    def __init__(self) -> None:
        self.limits: dict[str, bool] = {EVENT_APP: False, EVENT_REVIEW: False}

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
    return [str(item).strip() for item in values if str(item).strip()]


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        app_ids = _strings(inp.get('appIds'))
        if not app_ids:
            raise ValueError('Provide at least one Android package ID in appIds.')

        country = str(inp.get('country') or 'US').upper()
        language = str(inp.get('language') or 'en').lower()
        max_reviews = max(0, min(200, int(inp.get('maxReviewsPerApp', 20))))
        budget = Budget()

        proxy_input = inp.get('proxyConfiguration') or {'useApifyProxy': True}
        proxy_url = None
        proxy_factory = None
        if proxy_input.get('useApifyProxy') or proxy_input.get('proxyUrls'):
            proxy_config = await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
            if proxy_config:
                seed_url = await proxy_config.new_url(session_id=f'googleplay_{secrets.token_hex(4)}')
                proxy_url = seed_url

                # Google Play requests run in a worker thread.  Deriving a fresh
                # session URL from the seed avoids awaiting new_url() in that
                # thread, while still changing the Apify Proxy session/IP.
                def _next_proxy_url() -> str:
                    # Only swap the session ID; keep the password, host and any
                    # other username parameters (for example country-XX).
                    return re.sub(r'session-[^,:@]+', f'session-googleplay_{secrets.token_hex(4)}', seed_url, count=1)

                proxy_factory = _next_proxy_url
                Actor.log.info('Using Apify Proxy and rotating the session every 12 Google Play requests')

        client = GooglePlayClient(
            proxy_url=proxy_url,
            proxy_url_factory=proxy_factory,
            rotate_every=12,
            logger=lambda message: Actor.log.debug(message),
        )
        totals = {'apps': 0, 'reviews': 0, 'failedApps': 0, 'failedReviews': 0, 'requestedApps': len(app_ids)}
        try:
            for app_id in app_ids:
                if budget.limits[EVENT_APP] or budget.available(EVENT_APP, 1) < 1:
                    Actor.log.warning('The app-event spending limit was reached; stopping before another app request')
                    break
                try:
                    app = await asyncio.to_thread(client.get_app, app_id, language=language, country=country)
                except (GooglePlayError, ValueError) as exc:
                    totals['failedApps'] += 1
                    Actor.log.warning(f'App {app_id!r} failed and was not charged: {exc}')
                    continue

                app['type'] = 'app'
                app['scrapedAt'] = datetime.now(UTC).isoformat()
                if not await budget.charge_one(EVENT_APP):
                    break
                await Actor.push_data(app)
                totals['apps'] += 1

                allowed_reviews = budget.available(EVENT_REVIEW, max_reviews)
                if not max_reviews or not allowed_reviews:
                    continue
                try:
                    reviews = await asyncio.to_thread(
                        lambda: list(client.iter_reviews(
                            app['appId'], max_reviews=allowed_reviews, language=language, country=country,
                            sort='newest', score=None,
                        ))
                    )
                except (GooglePlayError, ValueError) as exc:
                    totals['failedReviews'] += 1
                    Actor.log.warning(f'Reviews for {app_id!r} failed and were not charged: {exc}')
                    continue

                for review in reviews:
                    if not await budget.charge_one(EVENT_REVIEW):
                        break
                    review.update({
                        'type': 'review',
                        'appId': app['appId'],
                        'appTitle': app['title'],
                        'appUrl': app['url'],
                        'language': language,
                        'country': country,
                        'scrapedAt': datetime.now(UTC).isoformat(),
                    })
                    await Actor.push_data(review)
                    totals['reviews'] += 1
        finally:
            client.close()

        await Actor.set_value('SUMMARY', totals)
        if not totals['apps']:
            raise RuntimeError('No public Google Play app data was collected. Check package IDs or retry later with Apify Proxy enabled.')


if __name__ == '__main__':
    asyncio.run(main())
