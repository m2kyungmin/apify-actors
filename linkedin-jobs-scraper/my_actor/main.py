"""LinkedIn Jobs Scraper Apify Actor entry point."""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime
from typing import Any

from apify import Actor

from .linkedin_client import LinkedInError, LinkedInJobsClient, SearchOptions

EVENT_JOB = 'job'


class Budget:
    def __init__(self) -> None:
        self.limit_reached = False

    def available(self, requested: int) -> int:
        try:
            allowed = Actor.get_charging_manager().calculate_max_event_charge_count_within_limit(EVENT_JOB)
        except Exception:  # noqa: BLE001
            allowed = None
        return requested if allowed is None else min(requested, max(0, allowed))

    async def charge_one(self) -> bool:
        if self.limit_reached or self.available(1) < 1:
            self.limit_reached = True
            return False
        result = await Actor.charge(EVENT_JOB, count=1)
        if result.event_charge_limit_reached:
            self.limit_reached = True
        return True


def _list(value: Any) -> list[str]:
    if not value:
        return []
    return [str(item) for item in value if str(item)] if isinstance(value, list) else [str(value)]


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        keywords = str(inp.get('keywords') or '').strip()
        if not keywords:
            raise ValueError('The `keywords` input is required and must not be empty.')

        requested_max = max(1, min(1000, int(inp.get('maxItems') or 25)))
        include_description = bool(inp.get('includeDescription', True))
        include_html = bool(inp.get('includeDescriptionHtml', False))
        options = SearchOptions(
            keywords=keywords,
            location=str(inp.get('location') or '').strip(),
            date_posted=str(inp.get('datePosted') or 'anyTime'),
            job_types=_list(inp.get('jobTypes')),
            experience_levels=_list(inp.get('experienceLevels')),
            workplace_types=_list(inp.get('workplaceTypes')),
            easy_apply_only=bool(inp.get('easyApplyOnly', False)),
            sort_by=str(inp.get('sortBy') or 'relevance'),
        )

        budget = Budget()
        max_items = budget.available(requested_max)
        if max_items <= 0:
            Actor.log.warning('Maximum pay-per-event charge limit leaves no jobs available; stopping before requests.')
            await Actor.set_value('SUMMARY', {'jobs': 0, 'failed': 0, 'requested': requested_max})
            return

        proxy_url = None
        proxy_factory = None
        proxy_input = inp.get('proxyConfiguration') or {}
        if proxy_input.get('useApifyProxy') or proxy_input.get('proxyUrls'):
            proxy_config = await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
            if proxy_config:
                seed_url = await proxy_config.new_url(session_id='seed0')
                proxy_url = seed_url

                def _sync_new_url() -> str:
                    if 'session-seed0' in seed_url:
                        return seed_url.replace('session-seed0', f'session-{secrets.token_hex(4)}')
                    return seed_url

                proxy_factory = _sync_new_url
                Actor.log.info('Using proxy with automatic session rotation')

        client = LinkedInJobsClient(
            proxy_url=proxy_url,
            proxy_url_factory=proxy_factory,
            max_retries=3,
            min_delay=0.4,
            rotate_every=20,
            logger=lambda message: Actor.log.debug(message),
        )
        seen: set[str] = set()
        totals = {'jobs': 0, 'failed': 0, 'duplicates': 0, 'searchPages': 0, 'requested': requested_max}
        start = 0
        # The guest endpoint currently returns 10 cards per response. Keep a few
        # extra pages for short/duplicate responses, while bounding all requests.
        max_pages = min(105, (max_items + 9) // 10 + 5)

        Actor.log.info(
            f'Searching LinkedIn public jobs for {keywords!r} in {options.location or "all locations"!r}; '
            f'up to {max_items} job(s)'
        )
        try:
            for _ in range(max_pages):
                if totals['jobs'] >= max_items or budget.limit_reached:
                    break
                try:
                    cards = await asyncio.to_thread(client.search_page, options, start=start)
                except LinkedInError as exc:
                    if totals['jobs']:
                        Actor.log.warning(f'Search page at offset {start} failed; returning collected jobs: {exc}')
                        break
                    raise
                totals['searchPages'] += 1
                if not cards:
                    Actor.log.info(f'No more search results at offset {start}')
                    break

                new_on_page = 0
                for card in cards:
                    if totals['jobs'] >= max_items or budget.limit_reached:
                        break
                    job_id = card['jobId']
                    if job_id in seen:
                        totals['duplicates'] += 1
                        continue
                    seen.add(job_id)
                    new_on_page += 1

                    item = dict(card)
                    if include_description:
                        try:
                            detail = await asyncio.to_thread(client.get_job, job_id)
                        except LinkedInError as exc:
                            totals['failed'] += 1
                            Actor.log.warning(f'Job {job_id}: detail failed and was not charged ({exc})')
                            continue
                        item.update(detail)
                        if not include_html:
                            item.pop('descriptionHtml', None)

                    item['searchKeywords'] = keywords
                    item['searchLocation'] = options.location or None
                    item['scrapedAt'] = datetime.now(UTC).isoformat()
                    if not await budget.charge_one():
                        Actor.log.warning('Maximum charge limit reached; stopping')
                        break
                    await Actor.push_data(item)
                    totals['jobs'] += 1
                    Actor.log.info(
                        f'[{totals["jobs"]}/{max_items}] {item["title"][:70]} at {item["companyName"][:50]}'
                    )

                if new_on_page == 0:
                    Actor.log.info('Search returned no new job IDs; stopping pagination')
                    break
                start += len(cards)
        finally:
            client.close()

        Actor.log.info(f'Done: {totals}')
        await Actor.set_value('SUMMARY', totals)
        if totals['jobs'] == 0:
            raise RuntimeError('No public jobs could be collected. Broaden the search or retry later if LinkedIn rate-limited the run.')
