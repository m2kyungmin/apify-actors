"""Browserless client for Google Ads Transparency Center RPC endpoints."""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import random
import re
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from httpx import AsyncClient, HTTPError

BASE = 'https://adstransparency.google.com'
RPC = BASE + '/anji/_/rpc/'
UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
)
HEADERS = {
    'User-Agent': UA,
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Content-Type': 'application/x-www-form-urlencoded',
    'Origin': BASE,
    'Referer': BASE + '/',
    'X-Same-Domain': '1',
}
FORMAT = {1: 'TEXT', 2: 'IMAGE', 3: 'VIDEO'}
FORMAT_BY_NAME = {v: k for k, v in FORMAT.items()}
# Google Ads geo-target criterion IDs for common countries.
GEO = {
    'US': 2840, 'KR': 2410, 'GB': 2826, 'DE': 2276, 'FR': 2250, 'JP': 2392, 'CA': 2124, 'AU': 2036,
    'IN': 2356, 'BR': 2076, 'IT': 2380, 'ES': 2724, 'NL': 2528, 'MX': 2484, 'SE': 2752, 'NO': 2578,
    'DK': 2208, 'FI': 2246, 'PL': 2616, 'PT': 2620, 'IE': 2372, 'CH': 2756, 'AT': 2040, 'BE': 2056,
    'CZ': 2203, 'GR': 2300, 'HU': 2348, 'RO': 2642, 'TR': 2792, 'IL': 2376, 'AE': 2784, 'SA': 2682,
    'ZA': 2710, 'NG': 2566, 'EG': 2818, 'AR': 2032, 'CL': 2152, 'CO': 2170, 'PE': 2604, 'ID': 2360,
    'MY': 2458, 'PH': 2608, 'SG': 2702, 'TH': 2764, 'VN': 2704, 'TW': 2158, 'HK': 2344, 'NZ': 2554,
    'UA': 2804, 'RU': 2643,
}
GEO_BY_ID = {v: k for k, v in GEO.items()}
ADVERTISER_ID_RE = re.compile(r'\b(AR\d{15,25})\b')
CREATIVE_ID_RE = re.compile(r'\b(CR\d{15,25})\b')


class Blocked(RuntimeError):
    """Google's CAPTCHA wall (302 -> google.com/sorry)."""


def _ts(obj: Any) -> str | None:
    """Timestamp objects look like {"1": seconds} (sometimes with nanos in "2")."""
    if isinstance(obj, dict) and '1' in obj:
        try:
            return datetime.fromtimestamp(int(obj['1']), tz=timezone.utc).isoformat()
        except (ValueError, TypeError, OSError):
            return None
    return None


def _yyyymmdd(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r'[^\d]', '', str(value))[:8]
    return int(digits) if len(digits) == 8 else None


class AdsTransparency:
    def __init__(
        self,
        *,
        proxy_url_factory: Callable[[], Awaitable[str | None]] | None = None,
        viewer_geo: int = 2840,
        min_delay: float = 0.6,
        log=None,
    ) -> None:
        self.viewer_geo = viewer_geo
        self.min_delay = min_delay
        self.proxy_url_factory = proxy_url_factory
        self.log = log
        self._client: AsyncClient | None = None
        self._last = 0.0
        self._calls_on_ip = 0
        # True while we bypass the proxy because Google is bouncing the whole proxy pool.
        self._direct = False
        # Google starts serving a CAPTCHA wall after roughly 20 cookieless RPC calls from one IP,
        # so with a rotating proxy we switch IPs well before that.
        self.rotate_every = 8

    async def _new_client(self) -> AsyncClient:
        proxy = None
        if self.proxy_url_factory and not self._direct:
            proxy = await self.proxy_url_factory()
        return AsyncClient(headers=HEADERS, timeout=30, follow_redirects=False, proxy=proxy)

    async def __aenter__(self) -> 'AdsTransparency':
        self._client = await self._new_client()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    async def _rotate(self, *, direct: bool | None = None) -> None:
        if direct is not None:
            self._direct = direct
        if self._client:
            await self._client.aclose()
        self._client = await self._new_client()
        self._calls_on_ip = 0

    async def rpc(self, method: str, payload: dict, *, retries: int = 4) -> dict:
        assert self._client is not None
        body = {'f.req': json.dumps(payload, separators=(',', ':'))}
        delay = 2.0
        loop = asyncio.get_event_loop()
        for attempt in range(1, retries + 1):
            if self.proxy_url_factory and self._calls_on_ip >= self.rotate_every:
                # Also returns to the proxy after a direct stretch, so one IP is never overused.
                await self._rotate(direct=False)
            wait = self.min_delay - (loop.time() - self._last)
            if wait > 0:
                await asyncio.sleep(wait + random.random() * 0.3)
            try:
                self._calls_on_ip += 1
                r = await self._client.post(RPC + method + '?authuser=', data=body)
            except HTTPError as exc:
                self._last = loop.time()
                if self.log:
                    self.log.warning(f'{method}: network error {exc!r} (attempt {attempt}/{retries})')
                await asyncio.sleep(delay)
                delay *= 2
                continue
            self._last = loop.time()
            if r.status_code == 200:
                data = r.json()
                if isinstance(data.get('2'), str) and 'Exception' in data['2']:
                    raise RuntimeError(f'{method}: server error {data["2"][:160]}')
                return data
            blocked = r.status_code in (301, 302) and 'google.com/sorry' in r.headers.get('location', '')
            if blocked or r.status_code in (429, 403, 503):
                if self.log:
                    self.log.warning(f'{method}: blocked (HTTP {r.status_code}), attempt {attempt}/{retries}')
                if self.proxy_url_factory:
                    if attempt >= 2 and not self._direct:
                        # Google sometimes bounces the whole shared proxy pool for an hour or more
                        # while the container's own egress IP is still fine - try that next.
                        if self.log:
                            self.log.info(f'{method}: proxy pool blocked, retrying over a direct connection')
                        await self._rotate(direct=True)
                        continue
                    await self._rotate(direct=False)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f'{method}: HTTP {r.status_code} {r.text[:160]}')
        hint = ('Google is bouncing both the Apify Proxy pool and the direct connection right now - retry in an hour'
                if self.proxy_url_factory else 'enable Apify Proxy in the input')
        raise Blocked(f'{method}: still blocked after {retries} attempts - {hint}')

    # -- advertisers ------------------------------------------------------------------
    async def search(self, query: str, max_advertisers: int = 10) -> tuple[list[dict], list[str]]:
        data = await self.rpc('SearchService/SearchSuggestions',
                              {'1': query, '2': max_advertisers, '3': 10, '5': {'1': 1}})
        advertisers, domains = [], []
        for row in data.get('1', []):
            if '1' in row:
                a = row['1']
                rng = (a.get('4') or {}).get('2') or {}
                advertisers.append({
                    'advertiserId': a.get('2'),
                    'advertiserName': a.get('1'),
                    'region': a.get('3'),
                    'adsCountMin': int(rng['1']) if '1' in rng else None,
                    'adsCountMax': int(rng['2']) if '2' in rng else None,
                    'isUnverified': bool(a.get('5', False)),
                })
            elif '2' in row:
                domains.append(row['2'].get('1'))
        return advertisers, [d for d in domains if d]

    async def get_advertiser(self, advertiser_id: str) -> dict:
        d = (await self.rpc('LookupService/GetAdvertiserById', {'1': advertiser_id, '3': {'1': 1}})).get('1', {})
        legal = d.get('9') or {}
        return {
            'type': 'advertiser',
            'advertiserId': d.get('1', advertiser_id),
            'advertiserName': d.get('2'),
            'legalName': legal.get('1') if isinstance(legal, dict) else None,
            'region': d.get('3'),
            'headquartersRegion': d.get('11'),
            'url': f'{BASE}/advertiser/{advertiser_id}?region=anywhere',
        }

    # -- creatives --------------------------------------------------------------------
    async def creatives_page(
        self,
        advertiser_id: str | None = None,
        *,
        domain: str | None = None,
        fmt: str | None = None,
        regions: list[int] | None = None,
        start: int | None = None,
        end: int | None = None,
        topic: str = 'ALL',
        page_size: int = 40,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None, tuple[int | None, int | None]]:
        f: dict[str, Any] = {'12': {'1': domain or '', '2': bool(domain)}}
        topic_id = 2 if topic.upper() == 'POLITICAL' else 1
        if advertiser_id:
            if topic_id == 2:
                f['1'] = advertiser_id
            else:
                f['13'] = {'1': [advertiser_id]}
        if fmt and fmt.upper() in FORMAT_BY_NAME:
            f['4'] = FORMAT_BY_NAME[fmt.upper()]
        if regions:
            f['8'] = regions
        if start:
            f['6'] = start
        if end:
            f['7'] = end
        payload: dict[str, Any] = {'2': page_size, '3': f, '7': {'1': topic_id, '2': 0, '3': self.viewer_geo}}
        if cursor:
            payload['4'] = cursor
        data = await self.rpc('SearchService/SearchCreatives', payload)
        items = [self.parse_creative(c) for c in data.get('1', [])]
        rng = (int(data['4']) if '4' in data else None, int(data['5']) if '5' in data else None)
        return items, data.get('2'), rng

    @staticmethod
    def parse_creative(c: dict) -> dict:
        prev = c.get('3', {}) or {}
        image_url = preview_url = None
        if isinstance(prev.get('3'), dict) and '2' in prev['3']:
            m = re.search(r'src="([^"]+)"', prev['3']['2'])
            image_url = html_lib.unescape(m.group(1)) if m else None
        if isinstance(prev.get('1'), dict) and '4' in prev['1']:
            preview_url = prev['1']['4']
        advertiser_id = c.get('1')
        creative_id = c.get('2')
        return {
            'type': 'ad',
            'advertiserId': advertiser_id,
            'advertiserName': c.get('12'),
            'creativeId': creative_id,
            'format': FORMAT.get(c.get('4'), f'UNKNOWN({c.get("4")})'),
            'firstShown': _ts(c.get('6')),
            'lastShown': _ts(c.get('7')),
            'daysShown': c.get('13'),
            'imageUrl': image_url,
            'previewUrl': preview_url,
            'url': f'{BASE}/advertiser/{advertiser_id}/creative/{creative_id}?region=anywhere',
        }

    # -- creative detail (variations, regions) ----------------------------------------------
    async def get_creative(self, advertiser_id: str, creative_id: str) -> dict:
        d = (await self.rpc('LookupService/GetCreativeById',
                            {'1': advertiser_id, '2': creative_id, '5': {'1': 1, '2': 0, '3': self.viewer_geo}})).get('1', {})
        variations = []
        for v in d.get('5', []) or []:
            if isinstance(v.get('3'), dict) and '2' in v['3']:
                m = re.search(r'src="([^"]+)"', v['3']['2'])
                variations.append({'type': 'image', 'url': html_lib.unescape(m.group(1)) if m else None})
            elif isinstance(v.get('1'), dict) and '4' in v['1']:
                variations.append({'type': 'preview', 'url': v['1']['4']})
        regions = []
        for r in d.get('17', []) or []:
            geo = r.get('1')
            regions.append({'geoId': geo, 'region': GEO_BY_ID.get(geo), 'lastShownDate': r.get('5')})
        return {
            'advertiserId': d.get('1', advertiser_id),
            'creativeId': d.get('2', creative_id),
            'format': FORMAT.get(d.get('8')),
            'lastShown': _ts(d.get('4')),
            'variations': variations,
            'regions': regions,
            'raw5': d.get('5'),
        }

    # -- OCR of archived ad screenshots ----------------------------------------------------
    async def ocr_image(self, image_url: str) -> str | None:
        """Read the text of an archived ad screenshot (tpc.googlesyndication.com/archive/simgad/...)."""
        assert self._client is not None
        try:
            r = await self._client.get(image_url, headers={'User-Agent': UA}, follow_redirects=True)
        except HTTPError:
            return None
        if r.status_code != 200 or not r.content:
            return None
        try:
            import io
            import pytesseract
            from PIL import Image, ImageOps

            def _run() -> str:
                img = Image.open(io.BytesIO(r.content)).convert('L')
                w, h = img.size
                if w < 900:  # upscale small screenshots - Tesseract likes ~30px tall glyphs
                    scale = 900 / w
                    img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
                img = ImageOps.autocontrast(img)
                return pytesseract.image_to_string(img, lang='eng', config='--psm 6')

            text = await asyncio.to_thread(_run)
        except Exception as exc:  # noqa: BLE001 - OCR is best-effort
            if self.log:
                self.log.debug(f'OCR failed for {image_url}: {exc!r}')
            return None
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{2,}', '\n', text).strip()
        # Drop OCR noise lines (single symbols, tiny fragments).
        lines = [ln.strip() for ln in text.split('\n') if len(re.sub(r'[^A-Za-z0-9가-힣]', '', ln)) >= 2]
        text = '\n'.join(lines)
        return text[:3000] or None

    # -- ad copy from the preview renderer ----------------------------------------------
    @staticmethod
    def _unescape_js(s: str) -> str:
        s = re.sub(r'\\x([0-9a-fA-F]{2})', lambda k: chr(int(k.group(1), 16)), s)
        s = re.sub(r'\\u([0-9a-fA-F]{4})', lambda k: chr(int(k.group(1), 16)), s)
        return s.replace('\\/', '/').replace("\\'", "'").replace('\\"', '"').replace('\\n', '\n')

    async def fetch_ad_text(self, preview_url: str) -> dict | None:
        """Fetch the ad preview renderer script and extract copy, links, images and video IDs.

        Two renderer flavours exist:
          * insertPreviewHtmlContent(..., '<html...>')   -> text/image/responsive ads
          * insertPreviewImageContent(id, el, 'https://i.ytimg.com/vi/<id>/hqdefault.jpg', w, h) -> video ads
        """
        assert self._client is not None
        try:
            r = await self._client.get(preview_url, headers={'User-Agent': UA, 'Referer': BASE + '/'}, follow_redirects=True)
        except HTTPError:
            return None
        if r.status_code != 200:
            return None
        js = r.text
        out: dict[str, Any] = {'adText': None, 'adLinks': [], 'adImages': [], 'youtubeVideoIds': [], 'videoThumbnailUrl': None}
        found = False

        for m in re.finditer(r"insertPreviewHtmlContent\((?:[^,]*,){0,6}?\s*'((?:[^'\\]|\\.)*)'", js, re.S):
            body = self._unescape_js(m.group(1))
            if '<' not in body:
                continue
            found = True
            body = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', body, flags=re.S | re.I)
            text = html_lib.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', body))).strip()
            text = re.sub(r'^Local Ad Rendering Service\s*', '', text)
            if text and not out['adText']:
                out['adText'] = text[:3000]
            out['adLinks'] += [html_lib.unescape(u) for u in re.findall(r'href="(https?://[^"]+)"', body)
                               if not re.search(r'corp\.google\.com|gstatic\.com|googleapis\.com|google\.com/', u)]
            out['adImages'] += [html_lib.unescape(u) for u in re.findall(r'<img[^>]+src="(https?://[^"]+)"', body)]
            out['youtubeVideoIds'] += re.findall(r'(?:youtube\.com/(?:embed|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]{11})', body)

        for m in re.finditer(r"insertPreviewImageContent\('[^']*',\s*'[^']*',\s*'((?:[^'\\]|\\.)*)'", js):
            url = self._unescape_js(m.group(1))
            found = True
            yt = re.search(r'ytimg\.com/vi/([A-Za-z0-9_-]{11})/', url)
            if yt:
                out['youtubeVideoIds'].append(yt.group(1))
                out['videoThumbnailUrl'] = out['videoThumbnailUrl'] or url
            else:
                out['adImages'].append(url)

        # Video IDs can also appear as plain ytimg thumbnails anywhere in the script.
        out['youtubeVideoIds'] += re.findall(r'ytimg\.com/vi/([A-Za-z0-9_-]{11})/', js)
        if not found and not out['youtubeVideoIds']:
            return None
        out['adLinks'] = list(dict.fromkeys(out['adLinks']))[:10]
        out['adImages'] = list(dict.fromkeys(out['adImages']))[:10]
        out['youtubeVideoIds'] = list(dict.fromkeys(out['youtubeVideoIds']))
        if out['youtubeVideoIds']:
            out['youtubeUrls'] = [f'https://www.youtube.com/watch?v={v}' for v in out['youtubeVideoIds']]
        return out
