# Automation templates for our Apify actors

Five ready-to-import **n8n** workflows (plus a Make.com recipe) that run our pay-per-event actors on a schedule, filter the result and deliver it to Google Sheets, Slack or email. All actors are unofficial scrapers published under the Apify account `kyungminlee`.

Files are in `integrations/n8n/`. Import with **Workflows > Import from file**.

| # | File | Actor | Schedule | Destination |
|---|------|-------|----------|-------------|
| 1 | `1-naver-shopping-rank-price-to-google-sheets.json` | `naver-shopping-search-scraper` | daily 08:00 | Google Sheets (append) |
| 2 | `2-google-ads-new-competitor-ads-to-slack.json` | `ads-transparency-scraper` | Mondays 09:00 | Slack |
| 3 | `3-taobao-price-change-alerts-to-email.json` | `taobao-catalog-scraper` | daily 07:00 | Email (SMTP) |
| 4 | `4-sec-edgar-new-filings-watchlist-to-slack.json` | `sec-edgar-filings-scraper` | weekdays 13:00 | Slack |
| 5 | `5-propertyfinder-new-listings-to-google-sheets.json` | `propertyfinder-listings-scraper` | Mondays 08:00 | Google Sheets (append) |

## What each template does

1. **Naver Shopping**: for each keyword, gets the top 50 products with `trackChanges` + `onlyChanged`, so only products that are new in the list or changed rank or price come back (and are charged). A Code node keeps product rows (free summary rows are dropped) and writes `date, keyword, rank, previousRank, rankChange, price, previousPrice, priceChange, isNewInTop, title, mallName, productId, url` to a sheet. The first run is a baseline and writes every product once.
2. **Google Ads Transparency**: monitors competitors by name (`searchQueries`) with `onlyNewAds`, ad copy on. One Slack message lists each new ad: advertiser, format, headline (OCR/parsed ad text), first-shown date and a link. No new ads means no message. The first run posts the baseline (up to 50 ads per advertiser).
3. **Taobao**: English keywords are translated (`translateKeywords`), prices converted to USD, and `onlyNewOrChangedPrices` returns only new or price-changed items. The Code node keeps items with `priceStatus = changed` and a change of at least 5% and sends an HTML table by email (previous and new CNY price, % change, USD price, link). To use Slack, swap the email node for a Slack node (see template 2).
4. **SEC EDGAR**: for a ticker watchlist (8-K, 10-K, 10-Q) with `onlyNewFilings`, posts one Slack message per day with ticker, form, filing date, 8-K item titles and a document link. The first run posts the last 30 days. The actor also has a financial screener (`screenerConcept`, `screenerMinGrowthPct`, ...). Add a second HTTP node with those fields if you want a market-wide screen.
5. **Property Finder**: searches one area with a bedroom and maximum price filter, then keeps only listings with `listedAt` in the last 7 days (the actor has no "new only" mode) and appends them to a sheet, cheapest first.

## Setup

**1. Apify token (all templates).** In n8n create a credential of type **Header Auth**: Name `Authorization`, Value `Bearer <YOUR_APIFY_TOKEN>` (Apify Console > Settings > API & Integrations). Select it on the HTTP Request node. The templates contain no token, only the placeholder credential `REPLACE_WITH_YOUR_CREDENTIAL_ID`, which n8n asks you to re-select after import.

**2. Destination credentials.**
- Google Sheets: add the *Google Sheets OAuth2* credential, paste your sheet ID, create the header row listed in the node notes.
- Slack: add the *Slack API* credential (bot token with `chat:write`), invite the bot to the channel, put the channel ID in the node.
- Email: add an *SMTP* credential and set the from/to addresses.

**3. Edit the input.** Keywords, domains, tickers, area and price are in the JSON body of the Apify HTTP node. Field names follow each actor's input schema.

**4. Test, then activate.** Run once manually (first runs are baselines in templates 1-4), then activate.

Notes from our own test calls:
- Use a **different history store name per template** (already set: `n8n-naver-rank-watch`, `n8n-ads-monitor`, `n8n-taobao-price-watch`, `n8n-sec-watchlist`). Actors cannot share one named store; a shared name made runs fail with a key-value store permission error.
- Input defaults from the Console form are **not** applied to API calls. The templates therefore set the needed fields explicitly (Property Finder requires `city`; it is set in the template and the run fails without it).
- The call uses `run-sync-get-dataset-items`, which Apify limits to 300 seconds. For bigger jobs lower the caps or switch to an async run plus *Get dataset items*. Query parameter `maxTotalChargeUsd=1` is a per-run spend cap; lower it as you like.
- We confirmed the endpoint form `POST https://api.apify.com/v2/acts/kyungminlee~<actor>/run-sync-get-dataset-items` with live calls for all five actors (see Verification below). We use the HTTP Request node, not the official Apify node, because its internal node type ID is not documented on the Apify n8n page (package `@apify/n8n-nodes-apify`, operations *Run an Actor* and *Get dataset items*). If you prefer it, use *Run an Actor* with the same JSON as custom input and wait for finish.

## Estimated monthly Apify cost (pay per event)

List prices before plan tier discounts: naver `product` $0.001; ads `ad` $0.002, `ad-text` $0.003, `advertiser` $0.003; taobao `item` $0.002; propertyfinder `listing` $0.0008; sec `filing` $0.001. Platform usage is included in these prices. The Ads `advertiser` event is charged once per advertiser per run (also when there are no new ads). Estimates assume the default inputs in the templates.

| # | Volume assumption | Typical / month | Worst case / month |
|---|-------------------|-----------------|--------------------|
| 1 Naver | 2 keywords x 50 products; ~30% change daily | ~$0.90 | $3.00 (all 100 rows daily) |
| 2 Google Ads | 2 advertisers; ~10 new ads each per week | ~$0.50 | ~$2.20 (50 new ads each per week); first run up to $0.60 |
| 3 Taobao | 2 keywords x 100 items; ~15% changed daily | ~$1.80 | $12.00 (all 200 charged daily); first run $0.40 |
| 4 SEC | 5 companies; ~0.5 new filing per day | ~$0.02 | $0.10 first-run baseline, then pennies |
| 5 Property Finder | 2 bedroom values x 150 listings weekly | ~$1.00 | same (all scraped listings are charged, then filtered) |

Together roughly **$4-5 per month** typical for all five. Paid Apify plans get tier discounts on these prices.

## Make.com equivalent

Make blueprints are harder to verify, so build the scenario from modules (about five minutes):

1. **Schedule** trigger (daily or weekly, same timing as the n8n template).
2. **Apify > Run an Actor**: connect with your Apify API token, pick the actor (`kyungminlee/<actor>`), choose *Input JSON* and paste the same input object from the n8n HTTP node, enable *Wait for the Actor run to finish*. Set the run's maximum charge if the module offers it.
3. **Apify > Get Dataset Items**: Dataset ID = `defaultDatasetId` from step 2. This returns one bundle per row.
4. **Filter** (between modules): same rule as the Code node, e.g. Naver `rankChange` or `priceChange` not equal 0; Ads `type = ad` and `isNew = true`; Taobao `priceStatus = changed`; SEC `isNew` not false; Property Finder `listedAt` after `addDays(now; -7)`.
5. **Destination**: *Google Sheets > Add a Row*, *Slack > Create a Message* (add a Text aggregator before it to combine bundles into one message), or *Email > Send an Email*.

Alternative with no Apify module: *HTTP > Make a request* with the same URL, Header `Authorization: Bearer <token>`, body type JSON, then *Parse response*.

## Verification

- Every n8n JSON parses, node names are unique, and every connection source and target exists. Code nodes were executed in Node.js against real actor output; the JSON body expression was evaluated.
- Live calls with `apify api` against all five actors (small inputs, `maxTotalChargeUsd=0.05`) returned dataset items in the shapes the filters expect. Template 2 was run twice with a small input and `onlyNewAds`: run 1 returned 4 new ads with ad text, run 2 returned 0 new ads (advertiser row and summary only). The exact input of templates 1, 3, 4 and 5 was also run once (small cap, separate test stores) and returned rows.
