# Pinterest Scraper: Pins, Boards, Search & Videos

> **Run it on Apify Store:** https://apify.com/kyungminlee/pinterest-scraper — pay per result, no setup. This folder is the Actor's source code (Python, `httpx`, Apify SDK).

Collect public Pinterest data without a browser, login, or cookies. Give the Actor
search keywords or Pinterest URLs and it returns clean JSON for every pin, board,
and profile: pin ID, title, description, original-resolution image URL, video URL,
outbound link and domain, save count, pinner, board, hashtags and creation date.
This is an **unofficial** tool and is not affiliated with or endorsed by Pinterest.

## What does Pinterest Scraper do?

- **Keyword search** – scrape pins for any search query, or switch the scope to
  video pins, boards, or profiles.
- **Pin pages** – paste `https://www.pinterest.com/pin/<id>/` (or a `pin.it`
  short link) and get the full pin record with engagement counts.
- **Boards** – paste a board URL and receive the board metadata (name,
  description, pin count, followers) plus its pins.
- **Profiles** – paste a profile URL and receive the profile metadata (bio,
  followers, pin and board counts) plus the pins the user created or saved.

Results land in the run's dataset and can be exported as JSON, CSV, Excel, or
fetched through the Apify API. No browser is started, so runs are fast and cheap:
a single request fetches up to 250 search results.

## Why use it?

- **No login or cookies** – only public, logged-out data is read, so your
  Pinterest account is never at risk.
- **Original image URLs** – `imageUrl` points at `i.pinimg.com/originals/...`,
  the highest resolution Pinterest serves; 736px and 236px variants are included
  as well.
- **Video support** – video pins include the MP4 URL, HLS playlist, dimensions,
  duration, and thumbnail.
- **Engagement metrics** – save count, repin count, comment count, share count
  and reactions, which most lightweight scrapers omit.
- **Pay only for results** – you are charged per pin, board, or profile that is
  actually stored. Failed sources cost nothing.

## How to use it

1. Open the Actor in Apify Console and enter one or more **Search queries**
   (for example `modern kitchen design`) and/or **Pin, board, profile or search
   URLs**.
2. Set **Maximum pins per source**. Each query, board, or profile stops after
   this many pins.
3. Optionally change the **Search scope** to video pins, boards, or profiles, and
   choose whether profile URLs should return created pins, saved pins, or only
   the profile record.
4. Click **Start**. Results appear in the **Dataset** tab; download them in the
   format you need or connect the run to your workflow with the API,
   integrations, or scheduler.

## Input

| Field | Type | Default | Notes |
|---|---|---|---|
| `searchQueries` | array of strings | – | Keywords to search. |
| `searchScope` | `pins` / `videos` / `boards` / `users` | `pins` | Kind of search result to collect. |
| `startUrls` | array of strings | – | Pin, board, profile, search page, or `pin.it` URLs. Any Pinterest country domain works. |
| `maxPinsPerSource` | integer | 50 | Cap per query, board, or profile (1–5000). |
| `profilePins` | `created` / `saved` / `none` | `created` | Which pins to collect for profile URLs. |
| `fetchPinDetails` | boolean | `true` | Search results lack engagement counts; enabling this fetches the full pin record for each search pin (one extra request per pin). |
| `proxyConfiguration` | object | Apify Proxy on | Proxy settings; the session rotates every 25 requests. |

Example input:

```json
{
  "searchQueries": ["modern kitchen design"],
  "startUrls": ["https://www.pinterest.com/homedecor/curate-your-kitchen-shelves/"],
  "maxPinsPerSource": 50,
  "fetchPinDetails": true
}
```

## Output

Each dataset item has a `type` of `pin`, `board`, or `profile`, plus `source`
(which query or URL produced it) and `scrapedAt`.

```json
{
  "type": "pin",
  "id": "435441857742802519",
  "url": "https://www.pinterest.com/pin/435441857742802519/",
  "title": "Mid-century Modern Home Paint Palette",
  "description": "Youthful Home Colors, Saturated Paint Tones, ...",
  "altText": "a kitchen with white cabinets and wood flooring",
  "link": "https://www.etsy.com/listing/1744469622/mid-century-modern-home-paint-palette",
  "domain": "etsy.com",
  "imageUrl": "https://i.pinimg.com/originals/ee/a9/36/eea936a0aacafb70bda6a41f6b50fb22.jpg",
  "imageWidth": 470,
  "imageHeight": 836,
  "isVideo": false,
  "video": null,
  "saveCount": 13098,
  "repinCount": 1173,
  "commentCount": 0,
  "shareCount": 87,
  "hashtags": [],
  "createdAt": "2025-11-30T17:03:38+00:00",
  "pinner": {"id": "435441995129835993", "username": "nmbtooth", "fullName": "Nichole Donaldson", "followerCount": 38, "profileUrl": "https://www.pinterest.com/nmbtooth/"},
  "board": {"id": "435441926410593228", "name": "Ideas for the House", "url": "https://www.pinterest.com/nmbtooth/ideas-for-the-house/"},
  "source": "pin:https://www.pinterest.com/pin/435441857742802519/",
  "scrapedAt": "2026-09-14T05:10:00+00:00"
}
```

## Data fields

**Pin** (`type: "pin"`): `id`, `url`, `title`, `description`, `altText`, `link`,
`domain`, `isUploaded`, `imageUrl` (original resolution), `imageWidth`,
`imageHeight`, `imageUrl736`, `imageUrl236`, `dominantColor`, `isVideo`,
`video` (`url`, `hlsUrl`, `width`, `height`, `durationMs`, `thumbnailUrl`),
`isIdeaPin`, `ideaPinPageCount`, `isPromoted`, `isRepin`, `saveCount`,
`repinCount`, `commentCount`, `shareCount`, `reactionCounts`, `hashtags`,
`createdAt`, `pinner` (`id`, `username`, `fullName`, `followerCount`,
`profileUrl`, `imageUrl`), `pinnerUsername`, `board` (`id`, `name`, `url`,
`pinCount`), `boardName`, `boardUrl`, `productPrice`, `productCurrency`.

**Board** (`type: "board"`): `id`, `url`, `name`, `description`, `pinCount`,
`sectionCount`, `followerCount`, `collaboratorCount`, `privacy`, `category`,
`coverImageUrl`, `lastPinnedAt`, `owner`, `ownerUsername`.

**Profile** (`type: "profile"`): `id`, `url`, `username`, `fullName`, `about`,
`websiteUrl`, `imageUrl`, `pinCount`, `boardCount`, `followerCount`,
`followingCount`, `videoPinCount`, `isVerifiedMerchant`, `isPartner`,
`createdAt`, `lastPinSaveAt`.

## Pricing

Pay per event: one `pin` event for every pin stored, one `board` event for every
board record, and one `profile` event for every profile record. Enabling
`fetchPinDetails` does not add events – the enriched pin is still one `pin`.
Failed URLs, unknown pins, and empty searches are never charged. See the
**Pricing** tab for the current rates and set a maximum charge per run to stay
within budget.

## Tips

- Search results are ranked by Pinterest for logged-out visitors, which is
  close to what an incognito browser sees for the same keyword.
- Set `fetchPinDetails` to `false` when you only need titles, links, and images;
  runs are then roughly ten times faster.
- Board and profile pins already include save counts, so no extra requests are
  made for them.
- Country domains such as `kr.pinterest.com` or `pinterest.co.uk` are accepted.
- Keep Apify Proxy enabled for larger runs; Pinterest tolerates modest volumes
  from a single IP, but rotating sessions avoids throttling.

## FAQ

**Is it legal to scrape Pinterest?** The Actor reads only data Pinterest shows to
anyone without an account. You are responsible for using the results in line with
Pinterest's terms and applicable laws, including personal-data regulations when
storing pinner profiles.

**Can it scrape private boards, secret pins, or my home feed?** No. Anything that
needs a login is out of scope by design.

**Why is `saveCount` null for some pins?** Search results do not carry engagement
data. Enable `fetchPinDetails` (default) to look up each search pin, or paste the
pin URL directly.

**Why does `link` come back empty?** Many pins are direct uploads with no outbound
website; `isUploaded` is `true` in that case.

**Does it download images or videos?** No, it returns URLs. Feed them to your own
downloader or another Actor if you need the files.

**Something stopped working.** Pinterest changes its internal API from time to
time. Open an issue on the Actor page and it will be investigated promptly.
