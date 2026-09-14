# Telegram Channel Scraper: Posts, Views, Reactions

> **Run it on Apify Store:** https://apify.com/kyungminlee/telegram-channel-scraper — pay per result, no setup. This folder is the Actor's source code (Python, `httpx`, Apify SDK).

## What does Telegram Channel Scraper do?

**Telegram Channel Scraper** solves one problem: getting the posts of a public Telegram channel as structured data without a Telegram account, bot token or API key. It reads the public web preview ([t.me/s/channel](https://t.me/s/telegram)), so 100 posts of a channel arrive in about 10 seconds and nothing you own can be rate-limited or banned.

Every post becomes one record with `channel`, `postId`, `url`, `date`, `text` (and optional HTML), `views`, `reactions` (emoji and counts), `mediaUrls`, `links`, `forwardedFrom`, `replyTo` and `editedAt`; an optional `channel-info` record adds title, description, subscriber count and avatar. Export JSON/CSV/Excel, call the REST API, or schedule the Actor and push new posts to Slack, Sheets, Make or your own pipeline.

## Why use Telegram Channel Scraper?

- **Monitor news and announcements** from crypto projects, software vendors, media outlets or government channels.
- **Track engagement**: views and reaction counts per post make it easy to rank content and spot what resonates.
- **Build datasets for research or AI**: export clean JSON/CSV of thousands of posts with dates and links for NLP, trend analysis or RAG pipelines.
- **Archive channels** on a schedule and keep your own searchable history.
- **Brand and competitor monitoring**: collect every mention across a set of channels and pipe it into Slack, Sheets or a database.

Because it reads the public preview, it only works for **public channels with the web preview enabled**. Private channels, invite links (`t.me/+...`), groups and bots are not supported.

## How to scrape Telegram channels

1. Open the Actor and enter one or more channels — usernames (`telegram`, `@durov`) or links (`https://t.me/durov`) both work.
2. Set **Max posts per channel** (default 50). Optionally limit by date with **Only posts newer than / older than**.
3. Click **Start**. Results appear in the **Dataset** tab within seconds.
4. Download the data as JSON, CSV, Excel, XML or HTML, or fetch it through the API.
5. Optional: add a **Schedule** to run daily and only collect new posts by setting `postsNewerThan` to yesterday.

## Input

| Field | Type | Default | Description |
|---|---|---|---|
| `channels` | array | `["telegram", "durov"]` | Public channel usernames or t.me URLs |
| `maxPostsPerChannel` | integer | `50` | Newest N posts per channel |
| `postsNewerThan` | string | – | `YYYY-MM-DD` or ISO date; scraping stops once older posts are reached |
| `postsOlderThan` | string | – | Skip posts published after this date |
| `includeChannelInfo` | boolean | `true` | Add one `type: "channel"` item per channel |
| `includeTextHtml` | boolean | `false` | Add the original formatted HTML of each post |
| `proxyConfiguration` | object | none | Usually not needed; enable Apify Proxy if you get HTTP 429 |

Example input:

```json
{
  "channels": ["telegram", "https://t.me/durov"],
  "maxPostsPerChannel": 100,
  "postsNewerThan": "2026-01-01"
}
```

## Output

Each post is one dataset item:

```json
{
  "type": "post",
  "channel": "durov",
  "postId": 548,
  "url": "https://t.me/durov/548",
  "date": "2026-09-10T16:05:00+00:00",
  "timestamp": 1789056300,
  "text": "Telegram has become the sponsor of Codeforces — the largest competitive programming platform in the world...",
  "author": null,
  "views": 772000,
  "viewsRaw": "772K",
  "reactions": [
    { "emoji": "⭐", "customEmojiId": null, "isPaid": true, "count": 10000 },
    { "emoji": "🔥", "customEmojiId": null, "isPaid": false, "count": 18100 }
  ],
  "reactionsTotal": 38220,
  "links": [{ "url": "https://codeforces.com/blog/entry/156620", "text": "Codeforces" }],
  "linkPreview": { "url": "https://codeforces.com/...", "siteName": "Codeforces", "title": "...", "description": "...", "imageUrl": "https://cdn4.telesco.pe/file/..." },
  "imageUrls": ["https://cdn4.telesco.pe/file/..."],
  "videoUrls": [],
  "videos": [],
  "documents": [],
  "poll": null,
  "forwardedFrom": null,
  "replyTo": null,
  "isForwarded": false,
  "isReply": false,
  "isEdited": false,
  "hasMedia": true
}
```

Channel items (`"type": "channel"`) contain `title`, `username`, `description`, `photoUrl`, `subscribers`, `photosCount`, `videosCount`, `filesCount`, `linksCount` and `isVerified`.

You can download the dataset in various formats such as JSON, HTML, CSV, or Excel.

## Data fields

| Field | Description |
|---|---|
| `postId`, `url`, `date`, `timestamp` | Post identity and publish time (UTC) |
| `text`, `textHtml` | Plain text (line breaks preserved) and optional original HTML |
| `views`, `viewsRaw` | View count as a number and as shown (`772K`) |
| `reactions`, `reactionsTotal` | Per-emoji counts, paid (star) reactions flagged, custom emoji IDs |
| `links`, `linkPreview` | Links inside the text and the rendered link preview card |
| `imageUrls`, `videos`, `videoUrls`, `documents`, `audio`, `stickerUrl`, `poll` | Media attached to the post (direct CDN URLs) |
| `forwardedFrom`, `replyTo` | Source of forwards and the quoted message for replies |
| `author`, `isEdited`, `isServiceMessage`, `hasMedia` | Post metadata |

## How much does it cost to scrape Telegram channels?

Pricing is **pay per event**: you pay a small amount per post and per channel-info item, nothing else. Scraping the latest 1,000 posts from a channel typically finishes in under a minute. Set **Max posts per channel** and date filters to control cost precisely; the Actor stops as soon as your limit or your run's maximum charge is reached.

## Tips

- Telegram's preview returns about 20 posts per page, so 1,000 posts means ~50 requests. Keep `maxPostsPerChannel` at what you actually need.
- For daily monitoring, schedule the Actor and set `postsNewerThan` to the previous day; the run will stop paging as soon as it hits older posts.
- Media URLs point at Telegram's CDN and can expire after a while — download files you need promptly.
- If you scrape hundreds of channels in one run and see HTTP 429 in the log, enable Apify Proxy in **Proxy configuration**.

## FAQ and disclaimers

**Does it work for private channels or groups?** No. Only public channels with the web preview enabled (`https://t.me/s/<channel>` must open in a browser).

**Do I need a Telegram account?** No. Nothing is logged in and no Telegram API credentials are used.

**Is it legal?** The Actor only collects publicly available data that Telegram itself serves to anyone on the web. You are responsible for how you use the data and for complying with Telegram's terms and applicable laws (e.g. GDPR when storing personal data). This is an unofficial tool and is not affiliated with Telegram.

**Something is missing or broken?** Telegram occasionally changes its preview markup. Please open an issue in the **Issues** tab with the channel name and we will fix it quickly. Custom features and integrations are available on request.
