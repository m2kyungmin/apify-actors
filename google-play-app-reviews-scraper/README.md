# Google Play Reviews Scraper: Ratings & Metadata

> **Run it on Apify Store:** https://apify.com/kyungminlee/google-play-app-reviews-scraper — pay per result, no setup. This folder is the Actor's source code (Python, `httpx`, Apify SDK).

## What does Google Play Reviews Scraper do?

**Google Play Reviews Scraper** collects the newest public reviews and the public store listing of any Android app from its package ID (for example `com.instagram.android`) — no Google account, Play Console access or browser. Review text is public, but reviewer names and avatars are deliberately not collected and obvious contact details are masked.

App records include `title`, `developer`, `developerUrl`, `rating`, `reviewCountText`, `downloadsText`, `contentRating`, `containsAds`, `summary`, `iconUrl` and `screenshots`; review records include `reviewId`, `score`, `content`, `createdAt`, `thumbsUpCount`, `appVersion`, `developerReply` and `developerRepliedAt`. Export JSON/CSV, call the API, or schedule the Actor to watch your own or a competitor's app for new 1-star reviews.

## Why use Google Play Reviews Scraper?

Package IDs are stable identifiers that make app research much easier to repeat than manually copying a store page. Use the results to monitor a competitor's public listing, compare ratings and download labels across markets, build a watch list, or analyse themes in a bounded sample of published feedback. The Actor separates app-level fields from review-level fields with a `type` value, so one dataset can support both a listing dashboard and a review analysis workflow.

## How to scrape Google Play reviews

For each requested package ID, the Actor requests the public Google Play details page and parses visible metadata. When reviews are requested, it uses Google Play's public page-data request to fetch small review pages and follows its opaque continuation token until it reaches the requested count or there are no more results. Google can rate-limit automated traffic, so Apify Proxy is enabled by default and the Actor rotates proxy sessions on longer runs. Failed apps or review pages are logged and never charged. The page-data format is undocumented and can change; retry later if Google changes it.

## Input

`appIds` is a required list of Android package IDs. `country` selects the two-letter storefront country, while `language` selects the display language. `maxReviewsPerApp` controls the maximum number of newest public review records per successful app; set it to zero when you only need app metadata. Keep the provided proxy configuration enabled for reliable Google traffic.

## Output

The dataset contains two kinds of rows. An `app` row is emitted once for every app whose public page was parsed. A `review` row is emitted for every public review successfully collected. Review rows reference their app through `appId`, `appTitle`, and `appUrl`. The `SUMMARY` key-value record reports successful and failed app/review counts for the run.

## Data fields

App rows can include the package ID, Google Play URL, title, summary and description, developer and developer links, icon and screenshots, rating, visible review-count text, download-count text, content rating, advertising label, country, language, and scrape time. Review rows include an opaque review ID, star score, public review text, review timestamp, helpful-vote count, app version, developer reply and reply time when present. The Actor intentionally excludes reviewer display names and avatars. It also masks obvious email addresses and telephone-number formats found in review text.

## Pricing

This Actor uses pay per event: one `app` event for each successfully saved app record and one `review` event for each successfully saved review record. The exact current prices appear on the Apify Actor page. Your run's maximum charge setting is checked before each event, so a budget limit can stop additional output without charging failed targets. Metadata-only runs incur app events but no review events.

## Tips

Start with one package ID and 20 reviews to confirm the selected country and language. Use a modest review limit when monitoring many apps, then increase it only for apps that need deeper analysis. Treat review text as user-generated public content; do not use it to identify or contact people. Store package IDs rather than mutable marketing URLs in your own workflow. Google may return different visible text by country, language, device context, or over time, so save `scrapedAt` with your analysis.

## FAQ

**Does this scrape private or logged-in data?** No. It reads only public Google Play pages and public review responses without authentication.

**Why are there fewer reviews than requested?** The app may have no more public records, Google may temporarily rate-limit the request, or the run's charge limit may have been reached.

**Can I use an app URL instead of a package ID?** Use the `id` parameter from its Google Play URL, such as `com.instagram.android`; package IDs avoid URL parsing ambiguity.

**Is the output official Google data?** No. This is an unofficial extraction of publicly displayed information and should be validated before making important decisions.
