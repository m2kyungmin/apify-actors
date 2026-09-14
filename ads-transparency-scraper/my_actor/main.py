"""Google Ads Transparency Center Scraper - Apify Actor entry point."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from apify import Actor

from .client import ADVERTISER_ID_RE, GEO, AdsTransparency, Blocked, _yyyymmdd

EVENT_ADVERTISER = 'advertiser'
EVENT_AD = 'ad'
EVENT_AD_TEXT = 'ad-text'


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
    return [str(v).strip() for v in value if str(v).strip()]


class Settings:
    def __init__(self, inp: dict) -> None:
        self.max_ads = int(inp.get('maxAdsPerAdvertiser') or 100)
        self.fmt = (inp.get('format') or 'ALL').upper()
        self.topic = (inp.get('topic') or 'ALL').upper()
        self.include_text = bool(inp.get('includeAdText', True))
        self.ocr = bool(inp.get('ocrAdText', True))
        self.debug = bool(inp.get('debug', False))
        self.start = _yyyymmdd(inp.get('startDate'))
        end = _yyyymmdd(inp.get('endDate'))
        if end:
            # The API takes an exclusive end date; make the user's "shown before" inclusive.
            end = int((datetime.strptime(str(end), '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d'))
        self.end = end
        self.regions: list[int] = []
        for code in _as_list(inp.get('regions')):
            geo = GEO.get(code.upper())
            if geo:
                self.regions.append(geo)
            else:
                Actor.log.warning(f'Unknown region code "{code}" - skipped. Use ISO alpha-2 codes like US, GB, DE.')


async def enrich_ad(api: AdsTransparency, item: dict, s: Settings) -> dict | None:
    """Fetch ad copy / video ID for one ad. Returns the extra fields or None when nothing was found."""
    preview_urls = [item['previewUrl']] if item.get('previewUrl') else []
    if not preview_urls:
        # Text ads carry no renderer URL in the listing; the detail call has variations + regions.
        try:
            detail = await api.get_creative(item['advertiserId'], item['creativeId'])
        except Blocked:
            raise
        except Exception as exc:  # noqa: BLE001
            Actor.log.debug(f'{item["creativeId"]}: detail failed ({exc})')
            detail = None
        if detail:
            if s.debug:
                Actor.log.info(f'DEBUG detail {item["creativeId"]} ({item["format"]}): variations={detail.get("variations")}')
            item['regions'] = detail.get('regions') or []
            preview_urls = [v['url'] for v in detail.get('variations', []) if v.get('type') == 'preview' and v.get('url')]
            if not item.get('imageUrl'):
                imgs = [v['url'] for v in detail.get('variations', []) if v.get('type') == 'image' and v.get('url')]
                item['imageUrl'] = imgs[0] if imgs else None
    extra: dict | None = None
    for purl in preview_urls[:2]:
        extra = await api.fetch_ad_text(purl)
        if s.debug:
            Actor.log.info(f'DEBUG preview {item["creativeId"]} ({item["format"]}): {purl[:120]} -> {str(extra)[:300]}')
        if extra and (extra.get('adText') or extra.get('youtubeVideoIds')):
            break
    if (not extra or not extra.get('adText')) and s.ocr and item.get('imageUrl'):
        # Google publishes text ads only as rendered screenshots -> OCR them.
        ocr_text = await api.ocr_image(item['imageUrl'])
        if ocr_text:
            extra = dict(extra or {})
            extra['adText'] = ocr_text
            extra['adTextSource'] = 'ocr'
    if not extra or not (extra.get('adText') or extra.get('youtubeVideoIds')):
        return None
    extra.setdefault('adTextSource', 'preview' if extra.get('adText') else None)
    return extra


async def collect_ads(
    api: AdsTransparency,
    budget: Budget,
    s: Settings,
    totals: dict,
    *,
    advertiser_id: str | None = None,
    domain: str | None = None,
    label: str,
) -> int:
    collected = 0
    cursor = None
    page = 0
    while collected < s.max_ads and not budget.limit_reached:
        items, cursor, rng = await api.creatives_page(
            advertiser_id, domain=domain, fmt=None if s.fmt == 'ALL' else s.fmt, regions=s.regions or None,
            start=s.start, end=s.end, topic=s.topic, page_size=min(40, s.max_ads - collected), cursor=cursor,
        )
        page += 1
        if page == 1:
            Actor.log.info(f'{label}: ~{rng[0]}-{rng[1]} ads match')
        if not items:
            break
        items = items[: s.max_ads - collected]
        accepted = await budget.charge(EVENT_AD, len(items))
        items = items[:accepted]
        if domain:
            for item in items:
                item['queryDomain'] = domain
        if s.include_text:
            for item in items:
                if budget.limit_reached:
                    break
                extra = await enrich_ad(api, item, s)
                if extra is None:
                    continue
                if not await budget.charge(EVENT_AD_TEXT, 1):
                    break
                item.update(extra)
                totals['adTexts'] += 1
        if items:
            await Actor.push_data(items)
            collected += len(items)
        Actor.log.info(f'{label}: page {page} -> {len(items)} ads (total {collected})')
        if not cursor:
            break
    return collected


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        queries = _as_list(inp.get('searchQueries'))
        advertiser_ids = [a.upper() for a in _as_list(inp.get('advertiserIds'))]
        for url in _as_list(inp.get('advertiserUrls')):
            found = ADVERTISER_ID_RE.findall(url)
            if found:
                advertiser_ids.extend(found)
            else:
                Actor.log.warning(f'No advertiser ID found in URL: {url}')
        domains = [re.sub(r'^https?://', '', d).split('/')[0].lower().removeprefix('www.') for d in _as_list(inp.get('domains'))]
        if not queries and not advertiser_ids and not domains:
            raise ValueError('Provide at least one of: searchQueries, advertiserIds, advertiserUrls, domains.')

        s = Settings(inp)
        max_advertisers = int(inp.get('maxAdvertisersPerQuery') or 1)
        include_advertiser = bool(inp.get('includeAdvertiserInfo', True))

        proxy_input = inp.get('proxyConfiguration') or {}
        proxy_factory = None
        if proxy_input.get('useApifyProxy') or proxy_input.get('proxyUrls'):
            proxy_config = await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
            if proxy_config:
                proxy_factory = proxy_config.new_url
                Actor.log.info('Using proxy')

        budget = Budget()
        totals = {'advertisers': 0, 'ads': 0, 'adTexts': 0, 'failed': 0}
        seen_advertisers: set[str] = set()

        async with AdsTransparency(proxy_url_factory=proxy_factory, log=Actor.log) as api:
            targets: list[tuple[str, str | None]] = []  # (advertiserId, nameHint)
            for query in queries:
                try:
                    advertisers, suggested_domains = await api.search(query, max_advertisers=20)
                except Blocked as exc:
                    Actor.log.error(str(exc))
                    raise
                except Exception as exc:  # noqa: BLE001
                    Actor.log.exception(f'Search "{query}" failed: {exc}')
                    totals['failed'] += 1
                    continue
                Actor.log.info(
                    f'"{query}": {len(advertisers)} advertiser match(es), domains: {suggested_domains[:5]} | '
                    + '; '.join(f'{a["advertiserName"]}[{a.get("region")}] ~{a.get("adsCountMax")}' for a in advertisers[:10])
                )
                if not advertisers:
                    Actor.log.warning(f'No advertisers found for "{query}" (domains suggested: {suggested_domains[:5]})')
                    continue
                # Google returns fuzzy matches; prefer verified advertisers with the most ads (the real brand).
                q = query.strip().lower()
                advertisers.sort(key=lambda a: (
                    a.get('isUnverified', False),
                    -(a.get('adsCountMax') or 0),
                    (a.get('advertiserName') or '').strip().lower() != q,
                ))
                picked = advertisers[:max_advertisers]
                Actor.log.info(
                    f'"{query}" -> ' + ', '.join(
                        f'{a["advertiserName"]} ({a["advertiserId"]}, ~{a.get("adsCountMin")}-{a.get("adsCountMax")} ads{", unverified" if a.get("isUnverified") else ""})'
                        for a in picked
                    )
                )
                targets.extend((a['advertiserId'], a['advertiserName']) for a in picked if a.get('advertiserId'))
            targets.extend((aid, None) for aid in advertiser_ids)

            for advertiser_id, name_hint in targets:
                if advertiser_id in seen_advertisers:
                    continue
                seen_advertisers.add(advertiser_id)
                if budget.limit_reached:
                    Actor.log.warning('Maximum charge limit reached - stopping')
                    break
                try:
                    if include_advertiser:
                        profile = await api.get_advertiser(advertiser_id)
                        if await budget.charge(EVENT_ADVERTISER, 1):
                            await Actor.push_data(profile)
                            totals['advertisers'] += 1
                    totals['ads'] += await collect_ads(api, budget, s, totals, advertiser_id=advertiser_id, label=name_hint or advertiser_id)
                except Blocked as exc:
                    Actor.log.error(str(exc))
                    totals['failed'] += 1
                    break
                except Exception as exc:  # noqa: BLE001
                    Actor.log.exception(f'{advertiser_id}: failed ({exc})')
                    totals['failed'] += 1

            for domain in domains:
                if budget.limit_reached:
                    break
                try:
                    totals['ads'] += await collect_ads(api, budget, s, totals, domain=domain, label=f'domain {domain}')
                except Blocked as exc:
                    Actor.log.error(str(exc))
                    totals['failed'] += 1
                    break
                except Exception as exc:  # noqa: BLE001
                    Actor.log.exception(f'domain {domain}: failed ({exc})')
                    totals['failed'] += 1

        Actor.log.info(f'Done: {totals}')
        await Actor.set_value('SUMMARY', totals)
        if totals['ads'] == 0 and totals['advertisers'] == 0:
            raise RuntimeError('Nothing was scraped. Check the input or enable Apify Proxy if Google blocked the requests.')
