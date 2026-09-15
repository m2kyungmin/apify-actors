# Apify Actors by kyungminlee

Source code of the pay-per-event web scrapers and tools published on the [Apify Store](https://apify.com/kyungminlee). Every Actor is browserless Python (`httpx` + Apify SDK), reads only public data, needs no login or cookies, and charges only for results actually delivered.

| Actor | Store page |
|---|---|
| [Telegram Channel Scraper: Posts, Views, Reactions](./telegram-channel-scraper/) | [apify.com/kyungminlee/telegram-channel-scraper](https://apify.com/kyungminlee/telegram-channel-scraper) |
| [Google Ads Transparency Scraper: Ad Copy OCR](./ads-transparency-scraper/) | [apify.com/kyungminlee/ads-transparency-scraper](https://apify.com/kyungminlee/ads-transparency-scraper) |
| [Google Trends Scraper: Trending Now & Regions](./google-trends-scraper/) | [apify.com/kyungminlee/google-trends-scraper](https://apify.com/kyungminlee/google-trends-scraper) |
| [YouTube Channel Transcript Scraper: Bulk Subtitles](./youtube-transcript-scraper/) | [apify.com/kyungminlee/youtube-transcript-scraper](https://apify.com/kyungminlee/youtube-transcript-scraper) |
| [LinkedIn Jobs Scraper: Full Descriptions, No Login](./linkedin-jobs-scraper/) | [apify.com/kyungminlee/linkedin-jobs-scraper](https://apify.com/kyungminlee/linkedin-jobs-scraper) |
| [TikTok Video Stats Scraper: Views, Likes by URL](./tiktok-profile-video-scraper/) | [apify.com/kyungminlee/tiktok-profile-video-scraper](https://apify.com/kyungminlee/tiktok-profile-video-scraper) |
| [Google Play Reviews Scraper: Ratings & Metadata](./google-play-app-reviews-scraper/) | [apify.com/kyungminlee/google-play-app-reviews-scraper](https://apify.com/kyungminlee/google-play-app-reviews-scraper) |
| [Pinterest Scraper: Pins, Boards, Search & Videos](./pinterest-scraper/) | [apify.com/kyungminlee/pinterest-scraper](https://apify.com/kyungminlee/pinterest-scraper) |
| [Threads Scraper: Profiles, Posts, Replies & Search](./threads-scraper/) | [apify.com/kyungminlee/threads-scraper](https://apify.com/kyungminlee/threads-scraper) |
| [AI Search Visibility Audit: ChatGPT & Perplexity](./ai-search-visibility-audit/) | [apify.com/kyungminlee/ai-search-visibility-audit](https://apify.com/kyungminlee/ai-search-visibility-audit) |

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
