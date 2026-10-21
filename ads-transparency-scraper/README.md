## What does Google Ads Transparency Center Scraper do?

**Built for agencies, in-house marketers and ad-intelligence teams who monitor competitors' Google and YouTube ads.** Get every Search, YouTube, Display, Shopping and Maps ad a competitor runs, with the **ad copy as text** (Search text ads are published only as screenshots, so this Actor reads them with OCR), format, image or video, countries, first and last shown dates and days active. It uses Google's public [Ads Transparency Center](https://adstransparency.google.com): no login, no browser, unofficial and not affiliated with Google.

**Typical uses:** new-campaign alerts on competitors, ad copy research for your own headlines, weekly agency reports, and brand-protection checks.

**Monitor mode (`onlyNewAds`):** turn it on and schedule the Actor weekly. The first run records every ad it sees; each later run outputs and charges **only ads that appeared since the last run**, flagged `isNew: true`, plus a free `advertiser-summary` row per competitor (new ads, total seen, latest first-shown date).

**What it costs:** $2 per 1,000 ads, plus $3 per 1,000 for extracted ad copy. Example: **monitoring 10 competitors weekly with about 50 new ads each is roughly $1 per week** (500 ads with copy ≈ $2.50 before plan discounts is the upper bound; most weeks fewer ads are new). A one-off pull of one competitor's 100 newest ads with copy is about $0.50. You only pay for ads actually delivered.

### Quick start

1. Enter competitor brand names or domains in **Advertiser names or domains to search** (or advertiser IDs or URLs).
2. Switch on **Only new ads (monitor mode)** and run once; this records the baseline.
3. Open **Schedules**, create a weekly schedule with the same input, and send the dataset to Slack, Sheets or email via integrations.

Each ad is one record: `adId`, `advertiserId`, `advertiserName`, `format`, `adText`, `imageUrl`, `videoId`, `regions`, `firstShown`, `lastShown`, `daysShown` and `detailUrl`; advertiser records add legal name, verification and domain. Export JSON, CSV or Excel, or call the API.

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
| `onlyNewAds` | boolean | `false` | Monitor mode: output and charge only ads not seen in earlier runs; adds a free `advertiser-summary` row |
| `historyStoreName` | string | `google-ads-monitor` | Named key-value store holding seen ad IDs (use one name per monitoring project) |
| `adTextConcurrency` | integer | `4` | Ads whose preview script / screenshot are downloaded at the same time (1–8); Google API calls stay sequential |

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

## Quick API example

Minimal input (also the prefill style used in the Apify Console):

```json
{"searchQueries": ["Shopify"], "maxAdsPerAdvertiser": 30}
```

Run synchronously and get the dataset items in one call (replace `<TOKEN>` with your Apify API token):

```
POST https://api.apify.com/v2/acts/kyungminlee~ads-transparency-scraper/run-sync-get-dataset-items?token=<TOKEN>
Content-Type: application/json

{"searchQueries": ["Shopify"], "maxAdsPerAdvertiser": 30}
```

AI agents using the Apify MCP server can call this Actor as `kyungminlee/ads-transparency-scraper` with the same JSON input.

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
| `isNew`, `firstSeenByMonitor` | Monitor mode only: `true` and the time the monitor first saw the ad |
| `recordType: "advertiser-summary"` | Monitor mode only, free: `newAds`, `totalSeen`, `latestFirstShown`, `firstRun` per advertiser or domain |

## How much does it cost to scrape Google Ads Transparency Center?

Pricing is **pay per event** — no subscription, no charge for ads that could not be fetched:

| Event | Price | When it is charged |
|---|---|---|
| Ad | $2.00 per 1,000 | Every ad saved to the dataset |
| Ad copy extracted | $3.00 per 1,000 | Ads whose text (OCR or HTML) or video ID was extracted |
| Advertiser profile | $0.003 each | One per advertiser when **Include advertiser info** is on |

Examples: one competitor's 100 newest ads with copy ≈ **$0.50**; 20 competitors × 100 ads ≈ **$10**; 1,000 ads without copy ≈ **$2**. Paid Apify plans get up to 30% off these prices automatically. Scraping 1,000 ads with ad copy typically takes 2–4 minutes. Use **Max ads per advertiser**, the format filter and the date range to control cost; the Actor stops as soon as your run's maximum charge is reached.

## Tips

- **No-code automation:** a ready-to-import [n8n workflow](https://github.com/m2kyungmin/apify-actors/blob/main/integrations/n8n/2-google-ads-new-competitor-ads-to-slack.json) runs this Actor on a schedule and sends a weekly Slack alert with competitors' new ads ([all templates](https://github.com/m2kyungmin/apify-actors/tree/main/integrations)).

- Search by **domain** (`brand.com`) when a brand name is ambiguous; the domain suggestions are logged for every query.
- Large advertisers (tens of thousands of ads) are returned newest first, so a small `maxAdsPerAdvertiser` gives you their current campaigns.
- To track new ads, turn on `onlyNewAds` and schedule a weekly run. In monitor mode `maxAdsPerAdvertiser` limits how many of the newest ads are checked, and scanning stops at the first page with nothing new. Keep the same `historyStoreName` for the same competitor list; delete the store to reset the baseline.
- Google shows a CAPTCHA wall after a few dozen requests from one IP. Apify Proxy is on by default and IPs are rotated automatically.
- Ad copy is fetched for several ads at once (`adTextConcurrency`, default 4), but every call to Google's own API is still made one at a time with a short delay, so the extra speed does not raise the block risk. Set it to `1` if you keep seeing `blocked (HTTP 302)` warnings.
- **Also check Bing and Snapchat ads:** the same competitor's search ads on Bing are in [Microsoft Ads Library Scraper](https://apify.com/kyungminlee/microsoft-ads-library-scraper), and their Snapchat ads (with EU impressions per country) are in [Snapchat Ads Library Scraper](https://apify.com/kyungminlee/snapchat-ads-library-scraper).

## FAQ and disclaimers

**Which ads are included?** Everything Google publishes in its Transparency Center: ads from verified advertisers shown on Google services, typically since mid‑2023, kept for the period Google defines.

**Can I get impressions or spend?** Google only publishes spend and impression ranges for political ads; those fields are not available for regular ads.

**Is it legal?** The Actor collects data that Google makes publicly available to everyone without login. You are responsible for how you use it and for complying with applicable laws and Google's terms. This is an unofficial tool and is not affiliated with Google.

**Something is missing or broken?** Google occasionally changes the Transparency Center. Please open an issue in the **Issues** tab with your input and we will fix it quickly. Custom features (e.g. keyword search across all advertisers, alerts) are available on request.
