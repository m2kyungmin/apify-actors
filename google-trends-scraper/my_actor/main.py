"""Google Trends Scraper - Apify Actor entry point."""

from __future__ import annotations

from typing import Any

from apify import Actor

from .trends_client import AsyncGoogleTrends, GoogleTrendsError, RateLimitError, TokenError

EVENT_KEYWORD = 'keyword'
EVENT_ROW = 'row'


class Budget:
    def __init__(self) -> None:
        self.limit_reached = False

    async def charge(self, event: str, count: int = 1) -> int:
        if count <= 0 or self.limit_reached:
            return 0
        try:
            allowed = Actor.get_charging_manager().calculate_max_event_charge_count_within_limit(event)
        except Exception:  # noqa: BLE001
            allowed = None
        to_charge = count if allowed is None else min(count, allowed)
        if to_charge <= 0:
            self.limit_reached = True
            return 0
        result = await Actor.charge(event, count=to_charge)
        if result.event_charge_limit_reached or to_charge < count:
            self.limit_reached = True
        return to_charge


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    out: list[str] = []
    for v in value:
        v = str(v).strip()
        if v and v.lower() not in {o.lower() for o in out}:
            out.append(v)
    return out


async def push_rows(budget: Budget, rows: list[dict], totals: dict) -> None:
    if not rows:
        return
    accepted = await budget.charge(EVENT_ROW, len(rows))
    rows = rows[:accepted]
    if rows:
        await Actor.push_data(rows)
        totals['rows'] += len(rows)


async def scrape_group(gt: AsyncGoogleTrends, budget: Budget, totals: dict, keywords: list[str], opts: dict) -> None:
    geo, tf, cat, gprop = opts['geo'], opts['timeframe'], opts['category'], opts['gprop']
    group = ' vs '.join(keywords) if len(keywords) > 1 else None
    base = {'geo': geo or 'worldwide', 'timeframe': tf, 'category': cat, 'searchType': opts['searchType']}
    if group:
        base['comparisonGroup'] = group

    if opts['iot']:
        series = await gt.interest_over_time(keywords, geo, tf, cat, gprop)
        rows = []
        for point in series:
            for kw in keywords:
                rows.append({'type': 'interestOverTime', 'keyword': kw, 'date': point['date'], 'formattedTime': point['formatted_time'],
                             'value': point['values'].get(kw), 'hasData': point['has_data'].get(kw), 'isPartial': point['is_partial'], **base})
        await push_rows(budget, rows, totals)
        Actor.log.info(f'{keywords}: interest over time -> {len(series)} points')

    if opts['region'] and not budget.limit_reached:
        resolution = opts['resolution']
        if resolution == 'COUNTRY' and geo:
            Actor.log.warning('COUNTRY resolution only works for worldwide searches - using REGION instead')
            resolution = 'REGION'
        regions = await gt.interest_by_region(keywords, geo, tf, cat, gprop, resolution=resolution, include_low_volume=opts['low_volume'])
        rows = []
        for r in regions:
            for kw in keywords:
                row = {'type': 'interestByRegion', 'keyword': kw, 'geoCode': r['geo_code'], 'geoName': r['geo_name'], 'resolution': resolution,
                       'value': r['values'].get(kw), 'hasData': r['has_data'].get(kw), **base}
                if r.get('coordinates'):
                    row['coordinates'] = r['coordinates']
                rows.append(row)
        await push_rows(budget, rows, totals)
        Actor.log.info(f'{keywords}: interest by region ({resolution}) -> {len(regions)} places')

    if opts['related_queries'] and not budget.limit_reached:
        related = await gt.related_queries(keywords, geo, tf, cat, gprop)
        rows = []
        for kw, lists in related.items():
            for kind in ('top', 'rising'):
                for rank, e in enumerate(lists.get(kind, []), start=1):
                    rows.append({'type': 'relatedQuery', 'keyword': kw, 'kind': kind, 'rank': rank, 'query': e.get('query'),
                                 'value': e.get('value'), 'formattedValue': e.get('formatted_value'), 'isBreakout': e.get('is_breakout', False),
                                 'link': ('https://trends.google.com' + e['link']) if e.get('link') else None, **base})
        await push_rows(budget, rows, totals)
        Actor.log.info(f'{keywords}: related queries -> {len(rows)} rows')

    if opts['related_topics'] and not budget.limit_reached:
        topics = await gt.related_topics(keywords, geo, tf, cat, gprop)
        rows = []
        for kw, lists in topics.items():
            for kind in ('top', 'rising'):
                for rank, e in enumerate(lists.get(kind, []) if isinstance(lists.get(kind), list) else [], start=1):
                    topic = e.get('topic') or {}
                    rows.append({'type': 'relatedTopic', 'keyword': kw, 'kind': kind, 'rank': rank, 'title': topic.get('title'),
                                 'topicType': topic.get('type'), 'mid': topic.get('mid'), 'value': e.get('value'),
                                 'formattedValue': e.get('formatted_value'), 'isBreakout': e.get('is_breakout', False), **base})
        if not rows:
            Actor.log.warning(f'{keywords}: Google returned no related topics (it only serves them to signed-in sessions)')
        await push_rows(budget, rows, totals)

    if opts['suggestions'] and not budget.limit_reached:
        rows = []
        for kw in keywords:
            for rank, s in enumerate(await gt.suggestions(kw), start=1):
                rows.append({'type': 'suggestion', 'keyword': kw, 'rank': rank, 'title': s.get('title'), 'topicType': s.get('type'), 'mid': s.get('mid'), **base})
        await push_rows(budget, rows, totals)


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        keywords = _as_list(inp.get('keywords'))
        trending = bool(inp.get('trendingNow', False))
        if not keywords and not trending:
            raise ValueError('Provide at least one keyword, or enable "Trending Now list".')

        gprop = (inp.get('searchType') or 'web').lower()
        opts = {
            'geo': (inp.get('geo') or '').strip().upper(),
            'timeframe': (inp.get('timeframe') or 'today 12-m').strip(),
            'category': int(inp.get('category') or 0),
            'gprop': '' if gprop == 'web' else gprop,
            'searchType': gprop,
            'iot': bool(inp.get('includeInterestOverTime', True)),
            'region': bool(inp.get('includeInterestByRegion', True)),
            'resolution': (inp.get('regionResolution') or 'REGION').upper(),
            'low_volume': bool(inp.get('includeLowVolumeRegions', False)),
            'related_queries': bool(inp.get('includeRelatedQueries', True)),
            'related_topics': bool(inp.get('includeRelatedTopics', False)),
            'suggestions': bool(inp.get('includeSuggestions', False)),
        }
        compare = bool(inp.get('compareKeywords', False))
        language = (inp.get('language') or 'en-US').strip()

        proxy_url = None
        proxy_input = inp.get('proxyConfiguration') or {}
        if proxy_input.get('useApifyProxy') or proxy_input.get('proxyUrls'):
            proxy_config = await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
            if proxy_config:
                proxy_url = await proxy_config.new_url()
                Actor.log.info('Using proxy')

        budget = Budget()
        totals = {'keywords': 0, 'rows': 0, 'failed': 0}
        groups: list[list[str]] = [keywords[i:i + 5] for i in range(0, len(keywords), 5)] if compare else [[k] for k in keywords]

        async with AsyncGoogleTrends(hl=language, tz=0, proxy=proxy_url, min_delay=2.0, max_retries=3) as gt:
            for group in groups:
                if budget.limit_reached:
                    Actor.log.warning('Maximum charge limit reached - stopping')
                    break
                if not await budget.charge(EVENT_KEYWORD, len(group)):
                    break
                try:
                    await scrape_group(gt, budget, totals, group, opts)
                    totals['keywords'] += len(group)
                except RateLimitError as exc:
                    Actor.log.error(f'{group}: {exc}. Google Trends is rate-limiting this IP - retry later or use a different proxy group.')
                    totals['failed'] += len(group)
                    break
                except TokenError as exc:
                    Actor.log.error(f'{group}: {exc}')
                    totals['failed'] += len(group)
                except GoogleTrendsError as exc:
                    Actor.log.error(f'{group}: {exc}')
                    totals['failed'] += len(group)
                except Exception as exc:  # noqa: BLE001
                    Actor.log.exception(f'{group}: failed ({exc})')
                    totals['failed'] += len(group)

            if trending and not budget.limit_reached:
                geo = (inp.get('trendingGeo') or 'US').strip().upper()
                hours = int(inp.get('trendingHours') or 24)
                try:
                    items = await gt.trending_now(geo, hours, active_only=bool(inp.get('trendingActiveOnly', False)))
                    rows = [{
                        'type': 'trending', 'geo': geo, 'hours': hours, 'rank': rank, 'title': t['title'], 'normalizedTitle': t['normalized_title'],
                        'searchVolume': t['search_volume'], 'increasePct': t['increase_pct'], 'startedAt': t['started_at_iso'],
                        'endedAt': t['ended_at_iso'], 'isActive': t['is_active'], 'breakdownKeywords': t['breakdown_keywords'],
                        'categories': t['categories'], 'categoryIds': t['category_ids'],
                        'url': f'https://trends.google.com/trends/explore?q={t["title"]}&geo={geo}',
                    } for rank, t in enumerate(items, start=1)]
                    await push_rows(budget, rows, totals)
                    Actor.log.info(f'Trending now {geo} ({hours}h) -> {len(rows)} trends')
                except GoogleTrendsError as exc:
                    Actor.log.error(f'Trending now failed: {exc}')
                    totals['failed'] += 1

        Actor.log.info(f'Done: {totals}')
        await Actor.set_value('SUMMARY', totals)
        if totals['rows'] == 0:
            raise RuntimeError('No data was collected. Check the keywords/timeframe or retry later if Google Trends rate-limited the run.')
