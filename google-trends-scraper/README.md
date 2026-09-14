# Google Trends Scraper: Trending Now & Regions

> **Run it on Apify Store:** https://apify.com/kyungminlee/google-trends-scraper — pay per result, no setup. This folder is the Actor's source code (Python, `httpx`, Apify SDK).

## What does Google Trends Scraper do?

**Google Trends Scraper** replaces the broken `pytrends` workflow: it talks to Google Trends' own data endpoints, so a keyword with a year of history returns in about 15 seconds with no browser, Google account or CAPTCHA, and Apify Proxy rotation absorbs Google's rate limits. It also exposes the **Trending Now** feed for any country, which has no official API at all.

Output rows are typed by `type`: `interestOverTime` (date, value, isPartial), `interestByRegion` (geo code, name, value), `relatedQuery` / `relatedTopic` (query, value, rising vs top), `suggestion` and `trending` (title, traffic, started, related queries, news articles) — each tagged with `keyword`, `geo` and `timeframe`. Export JSON/CSV, call the REST API, or schedule hourly Trending Now runs and forward new entries to Slack or a dashboard.

## Why use Google Trends Scraper?

- **Market and product research**: track demand for products, brands or topics across countries and time.
- **SEO and content planning**: discover rising related queries before they peak; compare keyword demand in one run.
- **Trend monitoring and alerts**: schedule hourly runs of *Trending Now* for any country and push new trends to Slack or a dashboard.
- **Investment and brand tracking**: build long-term time series for tickers, companies or competitors.
- **Data science**: clean numeric rows (0–100 index, dates in ISO format) that drop straight into pandas or BI tools.

## How to scrape Google Trends

1. Enter one or more **keywords** (each is scored independently; switch on **Compare keywords** for the classic side-by-side comparison of up to 5).
2. Choose a **location** (`US`, `GB`, `KR`, `US-CA`, or empty for worldwide) and a **timeframe** (`today 12-m`, `now 7-d`, `today 5-y`, `all`, or a date range).
3. Tick which data you need: interest over time, interest by region (with resolution), related queries, related topics, suggestions, Trending Now.
4. Click **Start**. Results appear in the **Dataset** tab; download or fetch them via API.

## Input

| Field | Default | Description |
|---|---|---|
| `keywords` | `["chatgpt", "claude ai"]` | Search terms |
| `compareKeywords` | `false` | Compare keywords on one shared scale (groups of 5) |
| `geo` | `""` (worldwide) | Country/region code |
| `timeframe` | `today 12-m` | Any Google Trends timeframe or `YYYY-MM-DD YYYY-MM-DD` |
| `category` | `0` | Google Trends category ID |
| `searchType` | `web` | `web`, `images`, `news`, `youtube`, `froogle` |
| `includeInterestOverTime` | `true` | Row per date |
| `includeInterestByRegion` | `true` | Row per region; `regionResolution` = `COUNTRY` / `REGION` / `CITY` / `DMA` |
| `includeRelatedQueries` | `true` | Top + rising related searches |
| `includeRelatedTopics` | `false` | Often empty (Google serves topics only to signed-in sessions) |
| `includeSuggestions` | `false` | Autocomplete entities |
| `trendingNow` | `false` | Live Trending Now list for `trendingGeo` over `trendingHours` (4/24/48/168) |
| `proxyConfiguration` | Apify Proxy on | Keep on: Google Trends rate-limits single IPs quickly |

Example:

```json
{
  "keywords": ["electric bike", "e-scooter"],
  "compareKeywords": true,
  "geo": "GB",
  "timeframe": "today 5-y",
  "regionResolution": "CITY"
}
```

## Output

One row per data point. Examples:

```json
{ "type": "interestOverTime", "keyword": "electric bike", "date": "2026-06-07T00:00:00Z", "value": 87, "isPartial": false, "geo": "GB", "timeframe": "today 5-y" }
{ "type": "interestByRegion", "keyword": "electric bike", "geoCode": "GB-ENG", "geoName": "England", "value": 100, "resolution": "REGION" }
{ "type": "relatedQuery", "keyword": "electric bike", "kind": "rising", "rank": 1, "query": "electric bike conversion kit", "value": 250, "formattedValue": "+250%", "isBreakout": false }
{ "type": "trending", "geo": "US", "hours": 24, "rank": 3, "title": "nfl scores", "searchVolume": 500000, "increasePct": 1000, "startedAt": "2026-09-13T17:00:00Z", "isActive": true, "categories": ["Sports"] }
```

You can download the dataset in various formats such as JSON, HTML, CSV, or Excel.

## Data fields

| Type | Fields |
|---|---|
| `interestOverTime` | `keyword`, `date`, `formattedTime`, `value` (0–100), `hasData`, `isPartial` |
| `interestByRegion` | `keyword`, `geoCode`, `geoName`, `resolution`, `value`, `coordinates` (cities) |
| `relatedQuery` / `relatedTopic` | `keyword`, `kind` (`top`/`rising`), `rank`, `query` or `title`, `value`, `formattedValue`, `isBreakout`, `link` |
| `suggestion` | `keyword`, `title`, `topicType`, `mid` |
| `trending` | `title`, `searchVolume`, `increasePct`, `startedAt`, `endedAt`, `isActive`, `breakdownKeywords`, `categories` |

Every row also carries `geo`, `timeframe`, `category`, `searchType` and, in compare mode, `comparisonGroup`.

## How much does it cost to scrape Google Trends?

Pricing is **pay per event**: a small fee per keyword and per row. A typical keyword (12 months, regions, related queries) produces about 50 time points, 50 regions and 50 related queries and takes 10–15 seconds. Switch off the data types you do not need to keep runs cheap; the Actor stops when your run's maximum charge is reached.

## Tips

- Values are relative (0–100 within each request). Use **Compare keywords** when you need keywords on the same scale; leave it off to get each keyword's own curve.
- `now 7-d` and shorter timeframes return hourly points; `today 5-y` returns weekly; `all` returns monthly.
- For city-level data set `regionResolution` to `CITY` and a country as `geo`.
- Schedule the Actor with `trendingNow` on and `trendingHours` = 4 to get fresh breakout trends every few hours.
- Google Trends throttles aggressively. The Actor spaces requests, retries with backoff and rotates proxy IPs; very large keyword lists (hundreds) may still hit limits — split them across runs.

## FAQ and disclaimers

**Are these absolute search volumes?** No. Google Trends publishes a normalized 0–100 index; Trending Now provides bucketed search volumes (e.g. 500K+).

**Why are related topics empty?** Google only returns related topics to signed-in browser sessions; related queries are unaffected.

**Is it legal?** The Actor collects data Google publishes openly on trends.google.com. You are responsible for how you use it and for complying with applicable laws and Google's terms. This is an unofficial tool, not affiliated with Google.

**Something broke?** Google changes Trends internals occasionally. Open an issue in the **Issues** tab with your input and it will be fixed quickly. Custom features (alerts, keyword sets, historical backfills) are available on request.
