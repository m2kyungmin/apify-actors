# TikTok Video Stats Scraper: Views, Likes by URL

> **Run it on Apify Store:** https://apify.com/kyungminlee/tiktok-profile-video-scraper — pay per result, no setup. This folder is the Actor's source code (Python, `httpx`, Apify SDK).

## What does TikTok Video Stats Scraper do?

**TikTok Video Stats Scraper** returns the numbers behind any public TikTok video you already know the URL of — views, likes, comments, shares and saves — plus the public statistics of any profile (followers, following, total likes, video count). It reads TikTok's public pages without login, cookies or a browser, so a batch of campaign or competitor URLs is measured in seconds and nothing of yours is at risk. It does not crawl a profile's entire feed; give it video URLs.

Video records contain `videoId`, `url`, `author`, `description`, `hashtags`, `music`, `durationSeconds`, `coverUrl`, `createdAt`, `viewCount`, `likeCount`, `commentCount`, `shareCount` and `saveCount`; profile records contain `username`, `nickname`, `bio`, `verified`, `avatarUrl`, `followerCount`, `followingCount`, `heartCount` and `videoCount`. Export JSON/CSV, call the API, or schedule the same URL list daily to build engagement time series.

## Why use it?

- Monitor a creator's public audience growth and profile statistics.
- Collect engagement metrics for a known list of campaign, competitor, or brand videos.
- Build dashboards from public video URLs without managing a TikTok account.
- Enrich a spreadsheet of public video links with captions, hashtags, audio, and counts.
- Schedule repeatable checks using Apify datasets, webhooks, and integrations.

The Actor deliberately uses public pages only. It neither requests credentials nor accepts cookies, and it does not use signed private feed endpoints. This keeps the input simple and makes failed WAF/challenge responses visible instead of silently charging for them.

## How to scrape TikTok public data

1. Open the Actor and select **Try for free**.
2. Add one or more usernames, such as `tiktok` or `natgeo`.
3. Optionally add complete public video URLs such as `https://www.tiktok.com/@user/video/123456789`.
4. Choose a maximum result count.
5. Start the run and export the default dataset as JSON, CSV, Excel, or through an Apify integration.

The default input contains one public username and one stable public video URL so it can be used for a quick health check.

## Input

```json
{
  "usernames": ["tiktok"],
  "videoUrls": ["https://www.tiktok.com/@scout2015/video/6718335390845095173"],
  "maxItems": 20,
  "proxyConfiguration": {"useApifyProxy": false}
}
```

`usernames` accepts names with or without `@`. `videoUrls` accepts only full public TikTok URLs containing `/video/<numeric-id>`; short/share URLs should be opened in a browser first and replaced with their final public URL. `maxItems` counts successful profile and video records together.

TikTok currently does not expose a stable, unsigned, paginated profile-video feed in the public profile state used here. For that reason, this Actor does **not** claim to enumerate every video for a username. Supply the video URLs you want to measure. This is intentional: it avoids relying on signed feed calls, authentication, or challenge-solving that can break without warning.

## Output

A profile record looks like this:

```json
{
  "type": "profile",
  "username": "tiktok",
  "nickname": "TikTok",
  "verified": true,
  "followerCount": 0,
  "followingCount": 0,
  "heartCount": 0,
  "videoCount": 0,
  "profileUrl": "https://www.tiktok.com/@tiktok"
}
```

An individual video record contains `videoId`, `url`, `description`, `hashtags`, `authorUsername`, `createdAt`, `playCount`, `likeCount`, `commentCount`, `shareCount`, `saveCount`, `durationSeconds`, `coverUrl`, and music fields. Counts and optional metadata can be absent when TikTok does not publish them for a particular video.

## Data fields

| Field | Description |
|---|---|
| `type` | Either `profile` or `video` |
| `username`, `nickname`, `bio` | Public profile identity and biography |
| `followerCount`, `followingCount`, `heartCount`, `videoCount` | Public profile statistics |
| `videoId`, `url`, `description`, `hashtags` | Public video identity and caption data |
| `playCount`, `likeCount`, `commentCount`, `shareCount`, `saveCount` | Public video engagement counts |
| `createdAt`, `durationSeconds`, `width`, `height` | Video timing and dimensions |
| `musicTitle`, `musicAuthor`, `musicOriginal` | Public audio metadata |
| `scrapedAt` | UTC timestamp at collection time |

## Pricing

The Actor uses pay-per-event pricing of **$0.0012 per successfully collected result** ($1.20 per 1,000 profiles or videos). A profile and a video each count as one result. Invalid URLs, duplicate input errors, WAF challenges, rate limits, unavailable pages, and parsing failures are never charged as result events. Apify platform compute and optional proxy costs may apply separately according to your Apify plan.

Use Apify's maximum charge setting to enforce a spending cap. The Actor checks the remaining result-event allowance before each request and stops when the cap is reached.

## Tips

- Start with one profile or a few video URLs to confirm availability.
- Keep video URLs in an external list if you want recurring metric snapshots.
- Retry later if TikTok serves a WAF challenge; this can vary by IP and time.
- Enable an Apify proxy only when direct data-center access is challenged. The Actor uses a new proxy session for every requested public page.
- Use profile records for creator-level metrics and video records for campaign-level metrics.

## FAQ

### Does it need a TikTok account?

No. It works only with information TikTok exposes on public web pages and does not accept usernames, passwords, cookies, or session tokens.

### Why did a username return no video list?

Public profile pages presently expose profile data but not a dependable unsigned video-feed pagination path. Add public individual video URLs for video-level results.

### Why can a run fail with a WAF challenge?

TikTok can vary its public-page access by IP, region, and traffic conditions. The Actor detects challenge pages, returns a clear error, and does not charge failed records. Retry later or use the optional proxy configuration.

### Is this official?

No. This is an unofficial tool with no affiliation to TikTok or ByteDance. You are responsible for complying with applicable laws, TikTok's terms, and reasonable request volumes when using public data.
