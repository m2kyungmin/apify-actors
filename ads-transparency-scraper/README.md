# Google Ads Transparency Scraper: Ad Copy OCR

> **Run it on Apify Store:** https://apify.com/kyungminlee/ads-transparency-scraper — pay per result, no setup. This folder is the Actor's source code (Python, `httpx`, Apify SDK).

## What does Google Ads Transparency Center Scraper do?

**Google Ads Transparency Center Scraper** answers "what ads is this company running right now?" without a browser: search an advertiser by name or domain (or paste advertiser IDs/URLs) and get every ad Google shows in its public [Transparency Center](https://adstransparency.google.com), including text extracted from image ads by OCR, which the Center itself does not expose as text.

Each ad is one record: `adId`, `advertiserId`, `advertiserName`, `format` (text, image, video), `adText` / `ocrText`, `imageUrl`, `videoId`, `regions`, `firstShown`, `lastShown`, `topic` and `detailUrl`; advertiser records add legal name, verification and domain. Export JSON/CSV, call the API, or schedule weekly runs and pipe new creatives to Slack, Sheets or your ad-intelligence dashboard.

## Why use Google Ads Transparency Center Scraper?

- **Competitor ad intelligence**: see exactly which ads a competitor is running, how long each has been live and where it is shown.
- **Creative research**: collect hundreds of ad headlines and descriptions from your industry to inspire your own copy.
- **Agency reporting**: monitor client and competitor accounts weekly and push new ads into Slack, Sheets or a dashboard.
- **Brand protection and compliance**: detect advertisers using your brand or domain, or audit political ads.
- **Market research and AI training data**: build datasets of ad copy, formats and run lengths across thousands of advertisers.

## How to scrape Google Ads Transparency Center

1. Enter one or more **search queries** (`Nike`, `shopify.com`) or paste **advertiser IDs** (`AR0162519528…`) or Transparency Center URLs.
2. Set **Max ads per advertiser** (default 100) and optionally filter by **format**, **regions** or **date range**.
3. Keep **Extract ad copy** on to get headlines, descriptions, links and video IDs for each ad.
4. Click **Start**. Results appear in the **Dataset** tab; download as JSON, CSV, Excel, XML or HTML, or fetch them via the API.

The best-matching advertiser for each query is chosen automatically (verified advertisers with the most ads first). To scrape several matching companies — for example regional subsidiaries — raise **Max advertisers per query**. For exact control use advertiser IDs.

## Input

| Field | Type | Default | Description |
|---|---|---|---|
| `searchQueries` | array | `["Shopify"]` | Brand names or domains, like the search box on the site |
| `advertiserIds` | array | – | IDs starting with `AR` |
| `advertiserUrls` | array | – | Transparency Center URLs containing an advertiser ID |
| `domains` | array | – | Website domains (`nike.com`): ads from *any* advertiser linking to that site |
| `maxAdvertisersPerQuery` | integer | `1` | How many matching advertisers to scrape per query |
| `maxAdsPerAdvertiser` | integer | `100` | Newest ads per advertiser |
| `format` | `ALL` / `TEXT` / `IMAGE` / `VIDEO` | `ALL` | Ad format filter |
| `regions` | array | – | ISO country codes, e.g. `["US", "GB"]` |
| `startDate`, `endDate` | `YYYY-MM-DD` | – | Only ads shown in this window |
| `topic` | `ALL` / `POLITICAL` | `ALL` | Political ads only |
| `includeAdText` | boolean | `true` | Extract copy, links, images and video IDs |
| `ocrAdText` | boolean | `true` | Read text ads (published only as screenshots) with OCR |
| `includeAdvertiserInfo` | boolean | `true` | Add one advertiser profile item per advertiser |
| `proxyConfiguration` | object | Apify Proxy on | Rotating IPs (recommended) |

Example:

```json
{
  "searchQueries": ["Shopify", "Samsung Electronics"],
  "maxAdsPerAdvertiser": 200,
  "format": "TEXT",
  "regions": ["US"],
  "startDate": "2026-01-01"
}
```

## Output

Each ad is one dataset item:

```json
{
  "type": "ad",
  "advertiserId": "AR01625195283841286145",
  "advertiserName": "Shopify Inc.",
  "creativeId": "CR12345678901234567890",
  "format": "TEXT",
  "firstShown": "2026-06-02T14:11:05+00:00",
  "lastShown": "2026-09-12T21:40:19+00:00",
  "daysShown": 103,
  "imageUrl": "https://tpc.googlesyndication.com/archive/simgad/1234567890",
  "adText": "Start Your Online Store - Try Shopify Free for 3 Days. Sell everywhere, all in one place.",
  "adLinks": ["https://www.shopify.com/free-trial"],
  "adImages": [],
  "youtubeVideoIds": [],
  "regions": [{ "geoId": 2840, "region": "US", "lastShownDate": 20260912 }],
  "url": "https://adstransparency.google.com/advertiser/AR01625195283841286145/creative/CR12345678901234567890?region=anywhere"
}
```

Video ads include `youtubeVideoIds`, `youtubeUrls` and `videoThumbnailUrl`. Advertiser items (`"type": "advertiser"`) contain `advertiserName`, `legalName`, `region`, `headquartersRegion` and the profile URL.

You can download the dataset in various formats such as JSON, HTML, CSV, or Excel.

## Data fields

| Field | Description |
|---|---|
| `advertiserId`, `advertiserName` | Verified advertiser identity from Google |
| `creativeId`, `url` | Unique ad ID and its public Transparency Center page |
| `format` | `TEXT`, `IMAGE` or `VIDEO` |
| `firstShown`, `lastShown`, `daysShown` | When the ad ran and for how long |
| `imageUrl` | Archived screenshot of the ad (text and image ads) |
| `adText`, `adTextSource` | Ad copy. Google publishes Search text ads only as screenshots, so they are read with OCR (`adTextSource: "ocr"`); HTML display ads are parsed directly (`"preview"`) |
| `adLinks`, `adImages` | Landing-page links and creative images found in HTML previews |
| `youtubeVideoIds`, `youtubeUrls`, `videoThumbnailUrl` | The YouTube video behind a video ad |
| `regions` | Countries where the ad was shown (when available) |

## How much does it cost to scrape Google Ads Transparency Center?

Pricing is **pay per event**: a small fee per ad, per extracted ad copy and per advertiser profile — no subscription. Scraping 1,000 ads with ad copy typically takes 2–4 minutes. Use **Max ads per advertiser**, the format filter and the date range to control cost; the Actor stops as soon as your run's maximum charge is reached.

## Tips

- Search by **domain** (`brand.com`) when a brand name is ambiguous; the domain suggestions are logged for every query.
- Large advertisers (tens of thousands of ads) are returned newest first, so a small `maxAdsPerAdvertiser` gives you their current campaigns.
- Schedule a weekly run with `startDate` set to last week to track only new ads.
- Google shows a CAPTCHA wall after a few dozen requests from one IP. Apify Proxy is on by default and IPs are rotated automatically.

## FAQ and disclaimers

**Which ads are included?** Everything Google publishes in its Transparency Center: ads from verified advertisers shown on Google services, typically since mid‑2023, kept for the period Google defines.

**Can I get impressions or spend?** Google only publishes spend and impression ranges for political ads; those fields are not available for regular ads.

**Is it legal?** The Actor collects data that Google makes publicly available to everyone without login. You are responsible for how you use it and for complying with applicable laws and Google's terms. This is an unofficial tool and is not affiliated with Google.

**Something is missing or broken?** Google occasionally changes the Transparency Center. Please open an issue in the **Issues** tab with your input and we will fix it quickly. Custom features (e.g. keyword search across all advertisers, alerts) are available on request.
