# Apify Actors by kyungminlee

Source code of the pay-per-event web scrapers and tools published on the [Apify Store](https://apify.com/kyungminlee). Every Actor is browserless Python (`httpx` + Apify SDK), reads only public data, needs no login or cookies, and charges only for results actually delivered.

| Actor | Store page |
|---|---|
| [Telegram Channel Scraper: Posts, Views, Reactions](./telegram-channel-scraper/) | [apify.com/kyungminlee/telegram-channel-scraper](https://apify.com/kyungminlee/telegram-channel-scraper) |
| [Google Ads Transparency Center Scraper: Ad Library](./ads-transparency-scraper/) | [apify.com/kyungminlee/ads-transparency-scraper](https://apify.com/kyungminlee/ads-transparency-scraper) |
| [Google Trends Scraper: Trending Now & Regions](./google-trends-scraper/) | [apify.com/kyungminlee/google-trends-scraper](https://apify.com/kyungminlee/google-trends-scraper) |
| [YouTube Channel Transcript Scraper: Bulk Subtitles](./youtube-transcript-scraper/) | [apify.com/kyungminlee/youtube-transcript-scraper](https://apify.com/kyungminlee/youtube-transcript-scraper) |
| [LinkedIn Jobs Scraper: Full Descriptions, No Login](./linkedin-jobs-scraper/) | [apify.com/kyungminlee/linkedin-jobs-scraper](https://apify.com/kyungminlee/linkedin-jobs-scraper) |
| [TikTok Video Stats Scraper: Views, Likes by URL](./tiktok-profile-video-scraper/) | [apify.com/kyungminlee/tiktok-profile-video-scraper](https://apify.com/kyungminlee/tiktok-profile-video-scraper) |
| [Google Play Reviews Scraper: Ratings & Metadata](./google-play-app-reviews-scraper/) | [apify.com/kyungminlee/google-play-app-reviews-scraper](https://apify.com/kyungminlee/google-play-app-reviews-scraper) |
| [Pinterest Scraper: Pins, Boards, Search & Videos](./pinterest-scraper/) | [apify.com/kyungminlee/pinterest-scraper](https://apify.com/kyungminlee/pinterest-scraper) |
| [Threads Scraper: Profiles, Posts, Replies & Search](./threads-scraper/) | [apify.com/kyungminlee/threads-scraper](https://apify.com/kyungminlee/threads-scraper) |
| [AI Search Visibility Audit: ChatGPT & Perplexity](./ai-search-visibility-audit/) | [apify.com/kyungminlee/ai-search-visibility-audit](https://apify.com/kyungminlee/ai-search-visibility-audit) |

## Automation templates (n8n / Make)

Ready-to-import [n8n](https://n8n.io) workflows in [`integrations/`](./integrations/) run these Actors on a schedule and send only what changed:

| Template | What it does |
|---|---|
| [Naver Shopping ranks → Google Sheets](./integrations/n8n/1-naver-shopping-rank-price-to-google-sheets.json) | Daily keyword rank and price changes on Naver Shopping |
| [New competitor Google ads → Slack](./integrations/n8n/2-google-ads-new-competitor-ads-to-slack.json) | Weekly alert with ads your competitors started running |
| [Taobao price changes → email](./integrations/n8n/3-taobao-price-change-alerts-to-email.json) | Daily price drops/rises for products you track |
| [SEC filings watchlist → Slack](./integrations/n8n/4-sec-edgar-new-filings-watchlist-to-slack.json) | New 10-K/10-Q/8-K filings of your watchlist |
| [Property Finder new listings → Google Sheets](./integrations/n8n/5-propertyfinder-new-listings-to-google-sheets.json) | Weekly new UAE listings under your price |

Setup, credentials and monthly cost estimates (typically $0.02–$2 per template) are in [integrations/README.md](./integrations/README.md); a Make.com equivalent is described there too.

## Running locally

Each folder is a standalone Actor:

```bash
cd telegram-channel-scraper
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
mkdir -p storage/key_value_stores/default
echo '{"channels": ["telegram"], "maxPostsPerChannel": 20}' > storage/key_value_stores/default/INPUT.json
.venv/bin/python -m my_actor
```

Deploy your own copy with the [Apify CLI](https://docs.apify.com/cli): `apify push` inside the folder.

## Notes

- Input schemas live in `.actor/input_schema.json`; every field has a description and a prefill that succeeds within minutes.
- Pay-per-event charging uses `Actor.charge()` with the run's spending limit respected; failed items are never charged.
- Undocumented site endpoints change; if an Actor stops working, open an issue on its Store page.

## License

[GNU AGPL-3.0](./LICENSE). You are free to read, run, modify and redistribute this code; if you deploy a modified version as a service (including on Apify Store), you must publish your changes under the same license. For a different licensing arrangement, open an issue.
