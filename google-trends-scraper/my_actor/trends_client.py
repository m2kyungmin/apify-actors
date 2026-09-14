"""
Browserless Google Trends client (httpx only), verified live on 2026-09-13.

Mirrors pytrends' feature set on the *current* internal JSON endpoints, with
sync (`GoogleTrends`) and async (`AsyncGoogleTrends`) clients that share the
same request builders / parsers. All results are plain dicts / lists.

================================================================================
ENDPOINT TABLE (all on https://trends.google.com, all GET unless noted)
================================================================================
| Purpose             | Path                                    | Params (query string)                                  | Response (after stripping ")]}'\\n") |
|---------------------|-----------------------------------------|--------------------------------------------------------|--------------------------------------|
| Warm-up / cookies   | /                                       | -                                                      | 302 -> /trends/ ; sets `NID` cookie  |
| Explore (tokens)    | /trends/api/explore                     | hl, tz, req=JSON{comparisonItem:[{keyword,geo,time}],  | {"widgets":[{id, token, request,     |
|                     |                                         |   category:int, property:""|images|news|youtube|froogle}|   title,...}]} ids: TIMESERIES,      |
|                     |                                         |                                                        |   GEO_MAP[_i], RELATED_QUERIES[_i],  |
|                     |                                         |                                                        |   RELATED_TOPICS[_i], TITLE_i        |
| Interest over time  | /trends/api/widgetdata/multiline        | hl, tz, token, req=JSON(widget.request)                | {"default":{"timelineData":[{time,   |
|                     |                                         |                                                        |   formattedTime, value:[..],         |
|                     |                                         |                                                        |   hasData:[..], isPartial?}]}}       |
| Interest by region  | /trends/api/widgetdata/comparedgeo      | hl, tz, token, req=JSON(widget.request with            | {"default":{"geoMapData":[{geoCode,  |
|                     |                                         |   resolution REGION|CITY|DMA|COUNTRY,                  |   geoName, value:[..], maxValueIndex,|
|                     |                                         |   includeLowVolumeGeos:bool)}                          |   hasData:[..], coordinates?}]}}     |
| Related queries /   | /trends/api/widgetdata/relatedsearches  | hl, tz, token, req=JSON(widget.request)                | {"default":{"rankedList":[           |
|   related topics    |                                         |   (keywordType QUERY -> queries, ENTITY -> topics)     |   {rankedKeyword:[{query|topic,      |
|                     |                                         |                                                        |   value, formattedValue, link}]}     |
|                     |                                         |                                                        |   x2 -> [TOP, RISING] ]}}            |
| Autocomplete        | /trends/api/autocomplete/{keyword}      | hl, tz                                                 | {"default":{"topics":[{mid,title,    |
|                     |                                         |                                                        |   type}]}}                           |
| Trending now        | POST /_/TrendsUi/data/batchexecute      | query: rpcids=i0OFE, source-path=/trending, hl, rt=c   | chunked: ")]}'\\n\\n<len>\\n[["wrb.fr",|
|   (replaces         |                                         | form body: f.req=[[["i0OFE",                           |   "i0OFE","<json string>",...]]      |
|    dailytrends)     |                                         |   "[null,null,\\"US\\",0,\\"en-US\\",24]",null,"generic"]]] | inner json: [null,[item,...]]        |
|                     |                                         |   args = [null,null,geo,0,hl,hours]                    | item = [title,null,geo,[start_ts],   |
|                     |                                         |                                                        |   [end_ts]|null,null,volume,null,    |
|                     |                                         |                                                        |   pct_increase,[breakdown kws],      |
|                     |                                         |                                                        |   [category_ids],[[article_id,lang,  |
|                     |                                         |                                                        |   geo],..],normalized_title]         |
| Trending RSS        | /trending/rss                           | geo                                                    | RSS 2.0 with ht:approx_traffic,      |
|   (fallback)        |                                         |                                                        |   ht:news_item, ht:picture           |
| DEAD                | /trends/api/dailytrends                 |                                                        | 404 since the 2024 redesign          |
| DEAD                | /trends/api/realtimetrends              |                                                        | 404                                  |

================================================================================
HEADERS / COOKIES / RATE LIMITS
================================================================================
* Required: a browser-like `User-Agent` and `Accept-Language`. Nothing else is
  strictly required, but we send `Referer` and `x-same-domain: 1` for
  batchexecute like the web app does.
* Cookie: `NID` is set by GET https://trends.google.com/ (302 -> /trends/).
  The API endpoints do work without it on a fresh IP, but sessions without it
  hit 429 much sooner. We always warm up first and re-warm on every 429.
* The `token` returned by explore is bound to the exact `request` JSON *except*
  `resolution` / `includeLowVolumeGeos` on comparedgeo (verified: changing
  `userConfig` -> 401, changing resolution -> 200). Never touch anything else.
* Explore stamps `userConfig.userType`. Non-signed-in HTTP sessions get
  `USER_TYPE_SCRAPER`; a logged-in Google session gets `USER_TYPE_LEGIT_USER`.
  Verified live: RELATED_TOPICS returns `{"rankedList": []}` for SCRAPER
  sessions (queries, timeseries, geo all still work). Related topics therefore
  only yields data if you inject your own signed-in Google cookies
  (`cookies=` ctor arg) -- this client never does that for you.
* 429: HTML "Error 429 (Bad Request)" body, no Retry-After. Most sensitive
  endpoint is relatedsearches (429 on the first call in a burst; fine with
  ~5 s spacing). Strategy: exponential backoff (5, 10, 20 s + jitter), new NID
  cookie on every retry, optional proxy rotation by constructing a new client
  per proxy. Keep >= 2-5 s between calls in an Apify actor.
* Categories arg (index 3) of the batchexecute request does NOT filter
  server-side (verified 566 vs 567 items); filter client-side on category_ids.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence
from urllib.parse import quote

import httpx

BASE = "https://trends.google.com"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
JSON_PREFIX = ")]}'"
TRENDING_RPC = "i0OFE"

GPROPS = {"", "web", "images", "news", "youtube", "froogle"}
GEO_RESOLUTIONS = {"COUNTRY", "REGION", "CITY", "DMA"}

# Category ids used in field 10 of trending-now items. Best-effort map taken
# from the Trends UI category filter (names ship in a JS bundle, not the HTML);
# verified spot-check: id 17 == Sports on live data.
TRENDING_CATEGORIES = {
    1: "Autos and Vehicles", 2: "Beauty and Fashion", 3: "Business and Finance",
    20: "Climate", 4: "Entertainment", 5: "Food and Drink", 6: "Games",
    7: "Health", 8: "Hobbies and Leisure", 9: "Jobs and Education",
    10: "Law and Government", 11: "Other", 13: "Pets and Animals",
    14: "Politics", 15: "Science", 16: "Shopping", 17: "Sports",
    18: "Technology", 19: "Travel and Transportation",
}


class GoogleTrendsError(Exception):
    """Base error."""


class RateLimitError(GoogleTrendsError):
    """Raised after retries are exhausted on HTTP 429."""


class TokenError(GoogleTrendsError):
    """Widget token rejected (400/401) - usually a tampered request."""


# ----------------------------------------------------------------------------
# Pure helpers: request builders + parsers (shared by sync and async clients)
# ----------------------------------------------------------------------------
def strip_prefix(text: str) -> str:
    if text.startswith(JSON_PREFIX):
        text = text[len(JSON_PREFIX):]
    return text.lstrip(",\r\n")


def parse_json(text: str) -> Any:
    return json.loads(strip_prefix(text))


def parse_batchexecute(text: str, rpcid: str) -> Any:
    """Parse the chunked batchexecute envelope and return the inner payload
    (already JSON-decoded) for `rpcid`."""
    body = strip_prefix(text)
    for line in body.split("\n"):
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            arr = json.loads(line)
        except json.JSONDecodeError:
            continue
        for env in arr:
            if isinstance(env, list) and len(env) >= 3 and env[0] == "wrb.fr" and env[1] == rpcid:
                if env[2] is None:
                    raise GoogleTrendsError(f"batchexecute {rpcid}: server returned an error envelope: {env[:6]}")
                return json.loads(env[2])
    raise GoogleTrendsError(f"batchexecute: no payload for rpcid {rpcid} in response")


def build_explore_req(keywords: Sequence[str], geo: str | Sequence[str], timeframe: str | Sequence[str],
                      category: int, gprop: str) -> dict:
    if isinstance(keywords, str):
        keywords = [keywords]
    if not 1 <= len(keywords) <= 5:
        raise ValueError("keywords: 1-5 items")
    if gprop == "web":
        gprop = ""
    if gprop not in GPROPS:
        raise ValueError(f"gprop must be one of {sorted(GPROPS)}")
    geos = [geo] * len(keywords) if isinstance(geo, str) else list(geo)
    tfs = [timeframe] * len(keywords) if isinstance(timeframe, str) else list(timeframe)
    if not (len(geos) == len(tfs) == len(keywords)):
        raise ValueError("geo/timeframe list lengths must match keywords")
    return {
        "comparisonItem": [{"keyword": k, "geo": g, "time": t} for k, g, t in zip(keywords, geos, tfs)],
        "category": int(category),
        "property": gprop,
    }


def build_trending_args(geo: str, hours: int, hl: str) -> list:
    if hours not in (4, 24, 48, 168):
        # the UI offers 4/24/48/168; other values are accepted but undocumented
        pass
    return [None, None, geo, 0, hl, int(hours)]


def widget_map(explore_resp: dict) -> dict[str, dict]:
    return {w["id"]: w for w in explore_resp.get("widgets", [])}


def _pick(widgets: dict[str, dict], base: str, idx: int) -> dict:
    """Single-keyword explores emit `RELATED_QUERIES`; comparisons emit
    `RELATED_QUERIES_0..n`. GEO_MAP (no suffix) is the compared map."""
    key = base if base in widgets and idx == 0 and f"{base}_0" not in widgets else f"{base}_{idx}"
    if key not in widgets:
        raise GoogleTrendsError(f"widget {key} not in explore response (ids={sorted(widgets)})")
    return widgets[key]


def parse_timeseries(payload: dict, keywords: Sequence[str]) -> list[dict]:
    out = []
    for row in payload.get("default", {}).get("timelineData", []):
        ts = int(row["time"])
        out.append({
            "time": ts,
            "date": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "formatted_time": row.get("formattedTime"),
            "values": {k: v for k, v in zip(keywords, row.get("value", []))},
            "has_data": {k: v for k, v in zip(keywords, row.get("hasData", []))},
            "is_partial": bool(row.get("isPartial", False)),
        })
    return out


def parse_geo(payload: dict, keywords: Sequence[str]) -> list[dict]:
    out = []
    for row in payload.get("default", {}).get("geoMapData", []):
        item = {
            "geo_code": row.get("geoCode"),
            "geo_name": row.get("geoName"),
            "values": {k: v for k, v in zip(keywords, row.get("value", []))},
            "has_data": {k: v for k, v in zip(keywords, row.get("hasData", []))},
            "max_value_index": row.get("maxValueIndex"),
        }
        if "coordinates" in row:
            item["coordinates"] = row["coordinates"]
        out.append(item)
    return out


def parse_related(payload: dict) -> dict[str, list[dict]]:
    """rankedList[0] = TOP, rankedList[1] = RISING."""
    lists = payload.get("default", {}).get("rankedList", [])

    def norm(entry: dict) -> dict:
        d: dict[str, Any] = {
            "value": entry.get("value"),
            "formatted_value": entry.get("formattedValue"),
            "link": entry.get("link"),
        }
        if "query" in entry:
            d["query"] = entry["query"]
        if "topic" in entry:
            d["topic"] = entry["topic"]  # {mid, title, type}
        d["is_breakout"] = entry.get("formattedValue") == "Breakout"
        return d

    top = [norm(e) for e in lists[0].get("rankedKeyword", [])] if len(lists) > 0 else []
    rising = [norm(e) for e in lists[1].get("rankedKeyword", [])] if len(lists) > 1 else []
    return {"top": top, "rising": rising}


def parse_trending(inner: Any, geo: str) -> list[dict]:
    items = inner[1] if isinstance(inner, list) and len(inner) > 1 and inner[1] else []
    out = []
    for it in items:
        if not isinstance(it, list) or len(it) < 13:
            continue
        start = it[3][0] if isinstance(it[3], list) and it[3] else None
        end = it[4][0] if isinstance(it[4], list) and it[4] else None
        cat_ids = list(it[10] or [])
        out.append({
            "title": it[0],
            "normalized_title": it[12],
            "geo": it[2] or geo,
            "started_at": start,
            "started_at_iso": _iso(start),
            "ended_at": end,
            "ended_at_iso": _iso(end),
            "is_active": end is None,
            "search_volume": it[6],           # bucketed: 100, 200, 500 ... 2000000
            "increase_pct": it[8],            # 50..1000 (1000 == "1,000%+")
            "breakdown_keywords": list(it[9] or []),
            "category_ids": cat_ids,
            "categories": [TRENDING_CATEGORIES.get(c, str(c)) for c in cat_ids],
            "news_article_ids": [a[0] for a in (it[11] or []) if isinstance(a, list) and a],
        })
    return out


def parse_trending_rss(xml_text: str) -> list[dict]:
    ns = {"ht": "https://trends.google.com/trending/rss"}
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):
        news = []
        for n in item.findall("ht:news_item", ns):
            news.append({
                "title": n.findtext("ht:news_item_title", default="", namespaces=ns),
                "url": n.findtext("ht:news_item_url", default="", namespaces=ns),
                "source": n.findtext("ht:news_item_source", default="", namespaces=ns),
                "picture": n.findtext("ht:news_item_picture", default="", namespaces=ns),
                "snippet": n.findtext("ht:news_item_snippet", default="", namespaces=ns),
            })
        out.append({
            "title": item.findtext("title"),
            "approx_traffic": item.findtext("ht:approx_traffic", namespaces=ns),
            "pub_date": item.findtext("pubDate"),
            "picture": item.findtext("ht:picture", namespaces=ns),
            "picture_source": item.findtext("ht:picture_source", namespaces=ns),
            "news": news,
        })
    return out


def _iso(ts: Optional[int]) -> Optional[str]:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def _is_html_error(resp: httpx.Response) -> bool:
    return resp.status_code >= 400 and "text/html" in resp.headers.get("content-type", "")


# ----------------------------------------------------------------------------
# Shared configuration
# ----------------------------------------------------------------------------
class _Config:
    def __init__(self, hl: str = "en-US", tz: int = 0, proxy: Optional[str] = None,
                 timeout: float = 30.0, max_retries: int = 3, backoff_base: float = 5.0,
                 min_delay: float = 2.0, user_agent: str = UA,
                 cookies: Optional[dict[str, str]] = None, bl: Optional[str] = None):
        """
        hl          UI language, e.g. "en-US", "ko".
        tz          minutes *west* of UTC (JS getTimezoneOffset). 0 = UTC, -540 = KST.
        proxy       "http://user:pass@host:port" (httpx `proxy=`). Use one client per proxy.
        max_retries retries on 429 / transient network errors.
        backoff_base seconds; wait = base * 2**attempt + jitter.
        min_delay   minimum spacing between consecutive API calls (seconds).
        cookies     extra cookies to inject (e.g. a signed-in session for related topics).
        bl          batchexecute build label; optional, server accepts requests without it.
        """
        self.hl, self.tz, self.proxy, self.timeout = hl, tz, proxy, timeout
        self.max_retries, self.backoff_base, self.min_delay = max_retries, backoff_base, min_delay
        self.user_agent, self.extra_cookies, self.bl = user_agent, cookies or {}, bl

    def headers(self) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "accept-language": f"{self.hl},{self.hl.split('-')[0]};q=0.9,en;q=0.8",
            "accept": "application/json, text/plain, */*",
            "referer": f"{BASE}/trends/explore",
        }

    def base_params(self) -> dict[str, Any]:
        return {"hl": self.hl, "tz": self.tz}

    def batchexecute_params(self) -> dict[str, Any]:
        p = {"rpcids": TRENDING_RPC, "source-path": "/trending", "hl": self.hl, "rt": "c"}
        if self.bl:
            p["bl"] = self.bl
        return p

    @staticmethod
    def batchexecute_headers() -> dict[str, str]:
        return {
            "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            "x-same-domain": "1",
            "origin": BASE,
            "referer": f"{BASE}/trending",
        }

    def backoff(self, attempt: int, resp: Optional[httpx.Response]) -> float:
        if resp is not None and resp.headers.get("retry-after", "").isdigit():
            return float(resp.headers["retry-after"])
        return self.backoff_base * (2 ** attempt) + random.uniform(0, 1.5)


# ----------------------------------------------------------------------------
# Sync client
# ----------------------------------------------------------------------------
class GoogleTrends:
    def __init__(self, **kwargs):
        self.cfg = _Config(**kwargs)
        self._client = httpx.Client(headers=self.cfg.headers(), proxy=self.cfg.proxy,
                                    timeout=self.cfg.timeout, follow_redirects=True)
        self._client.cookies.update(self.cfg.extra_cookies)
        self._warm = False
        self._last_call = 0.0
        self._explore_cache: dict[str, tuple[dict, list[str]]] = {}

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    # -- low level -----------------------------------------------------------
    def warmup(self, force: bool = False) -> dict[str, str]:
        """GET trends.google.com to obtain the NID cookie."""
        if self._warm and not force:
            return dict(self._client.cookies)
        if force:
            for k in list(self._client.cookies.keys()):
                if k not in self.cfg.extra_cookies:
                    del self._client.cookies[k]
        self._client.get(BASE + "/")
        self._warm = True
        return dict(self._client.cookies)

    def _throttle(self) -> None:
        wait = self.cfg.min_delay - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _request(self, method: str, path: str, *, params=None, data=None, headers=None) -> httpx.Response:
        self.warmup()
        last: Optional[httpx.Response] = None
        for attempt in range(self.cfg.max_retries + 1):
            self._throttle()
            try:
                resp = self._client.request(method, BASE + path, params=params, data=data, headers=headers)
            except httpx.TransportError as e:
                if attempt >= self.cfg.max_retries:
                    raise GoogleTrendsError(f"transport error: {e}") from e
                time.sleep(self.cfg.backoff(attempt, None))
                continue
            if resp.status_code == 429:
                last = resp
                if attempt >= self.cfg.max_retries:
                    break
                time.sleep(self.cfg.backoff(attempt, resp))
                self.warmup(force=True)
                continue
            if resp.status_code in (400, 401) and "widgetdata" in path:
                raise TokenError(f"{resp.status_code} on {path}: token/request mismatch or unsupported params")
            if resp.status_code >= 400:
                raise GoogleTrendsError(f"HTTP {resp.status_code} on {path}: {resp.text[:200]}")
            return resp
        raise RateLimitError(f"429 on {path} after {self.cfg.max_retries} retries")

    def _get_json(self, path: str, params: dict) -> Any:
        return parse_json(self._request("GET", path, params={**self.cfg.base_params(), **params}).text)

    # -- explore -------------------------------------------------------------
    def explore(self, keywords: str | Sequence[str], geo: str = "", timeframe: str = "today 12-m",
                category: int = 0, gprop: str = "") -> dict:
        """Returns {"keywords": [...], "widgets": {id: widget}, "raw": explore_json}."""
        req = build_explore_req(keywords, geo, timeframe, category, gprop)
        raw = self._get_json("/trends/api/explore", {"req": json.dumps(req, separators=(",", ":"))})
        kws = [c["keyword"] for c in req["comparisonItem"]]
        result = {"keywords": kws, "widgets": widget_map(raw), "raw": raw}
        self._explore_cache[json.dumps(req, sort_keys=True)] = (result["widgets"], kws)
        return result

    def _widgets(self, keywords, geo, timeframe, category, gprop) -> tuple[dict, list[str]]:
        req = build_explore_req(keywords, geo, timeframe, category, gprop)
        key = json.dumps(req, sort_keys=True)
        if key not in self._explore_cache:
            self.explore(keywords, geo, timeframe, category, gprop)
        return self._explore_cache[key]

    def _widgetdata(self, endpoint: str, widget: dict, patch: Optional[dict] = None) -> Any:
        req = dict(widget["request"])
        if patch:
            req.update(patch)
        return self._get_json(f"/trends/api/widgetdata/{endpoint}",
                              {"req": json.dumps(req, separators=(",", ":")), "token": widget["token"]})

    # -- public data methods -------------------------------------------------
    def interest_over_time(self, keywords, geo="", timeframe="today 12-m", category=0, gprop="") -> list[dict]:
        widgets, kws = self._widgets(keywords, geo, timeframe, category, gprop)
        return parse_timeseries(self._widgetdata("multiline", widgets["TIMESERIES"]), kws)

    def interest_by_region(self, keywords, geo="", timeframe="today 12-m", category=0, gprop="",
                           resolution: str = "REGION", include_low_volume: bool = False) -> list[dict]:
        resolution = resolution.upper()
        if resolution not in GEO_RESOLUTIONS:
            raise ValueError(f"resolution must be one of {sorted(GEO_RESOLUTIONS)}")
        widgets, kws = self._widgets(keywords, geo, timeframe, category, gprop)
        w = widgets.get("GEO_MAP") or _pick(widgets, "GEO_MAP", 0)
        patch: dict[str, Any] = {"resolution": resolution}
        if include_low_volume:
            patch["includeLowVolumeGeos"] = True
        return parse_geo(self._widgetdata("comparedgeo", w, patch), kws)

    def related_queries(self, keywords, geo="", timeframe="today 12-m", category=0, gprop="") -> dict[str, dict]:
        widgets, kws = self._widgets(keywords, geo, timeframe, category, gprop)
        return {k: parse_related(self._widgetdata("relatedsearches", _pick(widgets, "RELATED_QUERIES", i)))
                for i, k in enumerate(kws)}

    def related_topics(self, keywords, geo="", timeframe="today 12-m", category=0, gprop="") -> dict[str, dict]:
        """NOTE: returns empty top/rising for scraper-classified sessions (see module doc)."""
        widgets, kws = self._widgets(keywords, geo, timeframe, category, gprop)
        out = {}
        for i, k in enumerate(kws):
            try:
                w = _pick(widgets, "RELATED_TOPICS", i)
            except GoogleTrendsError:
                out[k] = {"top": [], "rising": [], "unavailable": True}
                continue
            out[k] = parse_related(self._widgetdata("relatedsearches", w))
        return out

    def suggestions(self, keyword: str) -> list[dict]:
        raw = self._get_json(f"/trends/api/autocomplete/{quote(keyword, safe='')}", {})
        return raw.get("default", {}).get("topics", [])

    def trending_now(self, geo: str = "US", hours: int = 24, category: Optional[int] = None,
                     active_only: bool = False) -> list[dict]:
        args = build_trending_args(geo, hours, self.cfg.hl)
        freq = json.dumps([[[TRENDING_RPC, json.dumps(args), None, "generic"]]])
        resp = self._request("POST", "/_/TrendsUi/data/batchexecute", params=self.cfg.batchexecute_params(),
                             data={"f.req": freq}, headers=self.cfg.batchexecute_headers())
        items = parse_trending(parse_batchexecute(resp.text, TRENDING_RPC), geo)
        if category is not None:
            items = [i for i in items if category in i["category_ids"]]
        if active_only:
            items = [i for i in items if i["is_active"]]
        return items

    def trending_rss(self, geo: str = "US") -> list[dict]:
        resp = self._request("GET", "/trending/rss", params={"geo": geo})
        return parse_trending_rss(resp.text)


# ----------------------------------------------------------------------------
# Async client (same surface, awaitable)
# ----------------------------------------------------------------------------
class AsyncGoogleTrends:
    def __init__(self, **kwargs):
        self.cfg = _Config(**kwargs)
        self._client = httpx.AsyncClient(headers=self.cfg.headers(), proxy=self.cfg.proxy,
                                         timeout=self.cfg.timeout, follow_redirects=True)
        self._client.cookies.update(self.cfg.extra_cookies)
        self._warm = False
        self._last_call = 0.0
        self._lock = asyncio.Lock()
        self._explore_cache: dict[str, tuple[dict, list[str]]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self): return self
    async def __aexit__(self, *a): await self.aclose()

    async def warmup(self, force: bool = False) -> dict[str, str]:
        if self._warm and not force:
            return dict(self._client.cookies)
        if force:
            for k in list(self._client.cookies.keys()):
                if k not in self.cfg.extra_cookies:
                    del self._client.cookies[k]
        await self._client.get(BASE + "/")
        self._warm = True
        return dict(self._client.cookies)

    async def _throttle(self) -> None:
        wait = self.cfg.min_delay - (time.monotonic() - self._last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()

    async def _request(self, method: str, path: str, *, params=None, data=None, headers=None) -> httpx.Response:
        async with self._lock:  # serialize: Google Trends punishes concurrency on one IP
            await self.warmup()
            for attempt in range(self.cfg.max_retries + 1):
                await self._throttle()
                try:
                    resp = await self._client.request(method, BASE + path, params=params, data=data, headers=headers)
                except httpx.TransportError as e:
                    if attempt >= self.cfg.max_retries:
                        raise GoogleTrendsError(f"transport error: {e}") from e
                    await asyncio.sleep(self.cfg.backoff(attempt, None))
                    continue
                if resp.status_code == 429:
                    if attempt >= self.cfg.max_retries:
                        break
                    await asyncio.sleep(self.cfg.backoff(attempt, resp))
                    await self.warmup(force=True)
                    continue
                if resp.status_code in (400, 401) and "widgetdata" in path:
                    raise TokenError(f"{resp.status_code} on {path}: token/request mismatch or unsupported params")
                if resp.status_code >= 400:
                    raise GoogleTrendsError(f"HTTP {resp.status_code} on {path}: {resp.text[:200]}")
                return resp
            raise RateLimitError(f"429 on {path} after {self.cfg.max_retries} retries")

    async def _get_json(self, path: str, params: dict) -> Any:
        return parse_json((await self._request("GET", path, params={**self.cfg.base_params(), **params})).text)

    async def explore(self, keywords, geo="", timeframe="today 12-m", category=0, gprop="") -> dict:
        req = build_explore_req(keywords, geo, timeframe, category, gprop)
        raw = await self._get_json("/trends/api/explore", {"req": json.dumps(req, separators=(",", ":"))})
        kws = [c["keyword"] for c in req["comparisonItem"]]
        result = {"keywords": kws, "widgets": widget_map(raw), "raw": raw}
        self._explore_cache[json.dumps(req, sort_keys=True)] = (result["widgets"], kws)
        return result

    async def _widgets(self, keywords, geo, timeframe, category, gprop):
        req = build_explore_req(keywords, geo, timeframe, category, gprop)
        key = json.dumps(req, sort_keys=True)
        if key not in self._explore_cache:
            await self.explore(keywords, geo, timeframe, category, gprop)
        return self._explore_cache[key]

    async def _widgetdata(self, endpoint: str, widget: dict, patch: Optional[dict] = None) -> Any:
        req = dict(widget["request"])
        if patch:
            req.update(patch)
        return await self._get_json(f"/trends/api/widgetdata/{endpoint}",
                                    {"req": json.dumps(req, separators=(",", ":")), "token": widget["token"]})

    async def interest_over_time(self, keywords, geo="", timeframe="today 12-m", category=0, gprop=""):
        widgets, kws = await self._widgets(keywords, geo, timeframe, category, gprop)
        return parse_timeseries(await self._widgetdata("multiline", widgets["TIMESERIES"]), kws)

    async def interest_by_region(self, keywords, geo="", timeframe="today 12-m", category=0, gprop="",
                                 resolution="REGION", include_low_volume=False):
        resolution = resolution.upper()
        if resolution not in GEO_RESOLUTIONS:
            raise ValueError(f"resolution must be one of {sorted(GEO_RESOLUTIONS)}")
        widgets, kws = await self._widgets(keywords, geo, timeframe, category, gprop)
        w = widgets.get("GEO_MAP") or _pick(widgets, "GEO_MAP", 0)
        patch: dict[str, Any] = {"resolution": resolution}
        if include_low_volume:
            patch["includeLowVolumeGeos"] = True
        return parse_geo(await self._widgetdata("comparedgeo", w, patch), kws)

    async def related_queries(self, keywords, geo="", timeframe="today 12-m", category=0, gprop=""):
        widgets, kws = await self._widgets(keywords, geo, timeframe, category, gprop)
        out = {}
        for i, k in enumerate(kws):
            out[k] = parse_related(await self._widgetdata("relatedsearches", _pick(widgets, "RELATED_QUERIES", i)))
        return out

    async def related_topics(self, keywords, geo="", timeframe="today 12-m", category=0, gprop=""):
        widgets, kws = await self._widgets(keywords, geo, timeframe, category, gprop)
        out = {}
        for i, k in enumerate(kws):
            try:
                w = _pick(widgets, "RELATED_TOPICS", i)
            except GoogleTrendsError:
                out[k] = {"top": [], "rising": [], "unavailable": True}
                continue
            out[k] = parse_related(await self._widgetdata("relatedsearches", w))
        return out

    async def suggestions(self, keyword: str) -> list[dict]:
        raw = await self._get_json(f"/trends/api/autocomplete/{quote(keyword, safe='')}", {})
        return raw.get("default", {}).get("topics", [])

    async def trending_now(self, geo="US", hours=24, category: Optional[int] = None, active_only=False):
        args = build_trending_args(geo, hours, self.cfg.hl)
        freq = json.dumps([[[TRENDING_RPC, json.dumps(args), None, "generic"]]])
        resp = await self._request("POST", "/_/TrendsUi/data/batchexecute", params=self.cfg.batchexecute_params(),
                                   data={"f.req": freq}, headers=self.cfg.batchexecute_headers())
        items = parse_trending(parse_batchexecute(resp.text, TRENDING_RPC), geo)
        if category is not None:
            items = [i for i in items if category in i["category_ids"]]
        if active_only:
            items = [i for i in items if i["is_active"]]
        return items

    async def trending_rss(self, geo="US") -> list[dict]:
        resp = await self._request("GET", "/trending/rss", params={"geo": geo})
        return parse_trending_rss(resp.text)


# ----------------------------------------------------------------------------
# Demo
# ----------------------------------------------------------------------------
def main() -> None:
    import sys
    kw, geo, tf = "python", "US", "today 12-m"
    proxy = None
    for a in sys.argv[1:]:
        if a.startswith("--proxy="):
            proxy = a.split("=", 1)[1]

    def show(label, fn):
        print(f"\n=== {label} ===")
        try:
            r = fn()
            print(json.dumps(r, ensure_ascii=False, indent=1)[:1200])
            return r
        except RateLimitError as e:
            print(f"RATE LIMITED: {e}")
        except GoogleTrendsError as e:
            print(f"ERROR: {e}")

    with GoogleTrends(hl="en-US", tz=0, proxy=proxy, min_delay=3.0, max_retries=2) as gt:
        print("cookies after warmup:", list(gt.warmup().keys()))
        show("suggestions('python')", lambda: gt.suggestions(kw)[:5])
        def _explore():
            ex = gt.explore(kw, geo, tf)
            return {"keywords": ex["keywords"], "widget_ids": sorted(ex["widgets"]),
                    "user_type": ex["widgets"]["TIMESERIES"]["request"].get("userConfig", {}).get("userType")}
        show("explore", _explore)
        iot = show("interest_over_time", lambda: gt.interest_over_time(kw, geo, tf))
        if iot:
            print(f"... {len(iot)} points, last: {iot[-1]}")
        show("interest_by_region REGION (top 5)",
             lambda: sorted(gt.interest_by_region(kw, geo, tf), key=lambda r: -r['values'][kw])[:5])
        show("interest_by_region DMA (top 3)",
             lambda: sorted(gt.interest_by_region(kw, geo, tf, resolution="DMA"), key=lambda r: -r['values'][kw])[:3])
        show("related_queries", lambda: {kw: {k: v[:3] for k, v in gt.related_queries(kw, geo, tf)[kw].items()}})
        show("related_topics (empty for scraper sessions)",
             lambda: {kw: {k: v[:3] if isinstance(v, list) else v for k, v in gt.related_topics(kw, geo, tf)[kw].items()}})
        tn_all: list = []

        def _trending():
            tn_all.extend(gt.trending_now("US", 24))
            return tn_all[:3]
        if show("trending_now US 24h (top 3)", _trending) is not None:
            print(f"... total items: {len(tn_all)}, active: {sum(i['is_active'] for i in tn_all)}")
        show("trending_rss US (top 2)", lambda: gt.trending_rss("US")[:2])


if __name__ == "__main__":
    main()
