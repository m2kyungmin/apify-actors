# LinkedIn Jobs Scraper: Full Descriptions, No Login

> **Run it on Apify Store:** https://apify.com/kyungminlee/linkedin-jobs-scraper — pay per result, no setup. This folder is the Actor's source code (Python, `httpx`, Apify SDK).

## What does LinkedIn Jobs Scraper do?

**LinkedIn Jobs Scraper** gives you public LinkedIn job postings with **full descriptions** for any keyword and location, using LinkedIn's guest search — no account, cookies or browser, so nothing of yours can be flagged. Filter by date posted, workplace type (remote/hybrid/on-site), job type, experience level and Easy Apply; 25 jobs with details take about 30 seconds.

Each job is one record: `jobId`, `title`, `company`, `companyUrl`, `location`, `workplaceType`, `seniorityLevel`, `employmentType`, `postedAt`, `applicantCount`, `description` (text, HTML optional), `salary` when shown, `applyUrl` and `jobUrl`. Export JSON/CSV, call the API from your ATS or dashboard, or schedule daily searches and push new postings to Slack, Sheets or email.

## Why use LinkedIn Jobs Scraper?

- Monitor hiring demand for technologies, roles, or markets.
- Build job alerts and internal recruiting dashboards.
- Research which companies are hiring in a city or country.
- Compare remote, hybrid, and on-site opportunities.
- Analyze employment types, experience levels, industries, and posting activity.
- Feed current public job data into spreadsheets, databases, or automated workflows.

It uses fast HTTP requests instead of a headless browser, keeping compute overhead low. Duplicate job IDs are removed within each run. Failed detail pages are skipped and are not charged as result events.

## How to scrape LinkedIn jobs

1. Open the Actor and select **Try for free**.
2. Enter a keyword such as `software engineer`.
3. Enter a location such as `United States`, `Berlin`, or `South Korea`.
4. Set the maximum number of jobs and any optional filters.
5. Click **Start**.
6. Download the dataset or connect it to another Apify integration.

No LinkedIn credentials are requested or accepted.

## Input

The Input tab provides a form for all options. A minimal request looks like this:

```json
{
  "keywords": "Python developer",
  "location": "United States",
  "maxItems": 25,
  "datePosted": "pastWeek",
  "workplaceTypes": ["remote"],
  "includeDescription": true
}
```

Available filters include date posted, job type, experience level, workplace type, Easy Apply only, and relevance or newest-first sorting. Set `includeDescription` to `false` for faster search-card-only collection. Set `includeDescriptionHtml` to `true` when you need the public description markup as well as plain text.

The optional `proxyConfiguration` is off by default because the public guest endpoints normally work from Apify data-center IPs. Enable an Apify or custom proxy for a larger run if LinkedIn begins rate-limiting requests.

## Output

Each charged dataset item represents one successfully collected job posting:

```json
{
  "jobId": "4465379983",
  "title": "Senior Software Engineer - Python",
  "companyName": "Venmo",
  "location": "Austin, TX",
  "postedDate": "2026-09-10",
  "applicantCount": 111,
  "seniorityLevel": "Not Applicable",
  "employmentType": "Full-time",
  "jobFunction": "Engineering",
  "industries": "Financial Services",
  "descriptionText": "The Company ...",
  "url": "https://www.linkedin.com/jobs/view/...",
  "scrapedAt": "2026-09-13T09:30:00+00:00"
}
```

You can download the dataset in various formats such as JSON, HTML, CSV, or Excel.

## Data fields

| Field | Description |
|---|---|
| `jobId` | Stable numeric LinkedIn job posting ID |
| `title` | Public job title |
| `companyName`, `companyUrl` | Hiring company name and public company URL |
| `location` | Location displayed on the search card |
| `postedDate`, `postedTime` | Machine-readable date and relative posting time when available |
| `descriptionText`, `descriptionHtml` | Full public description as text and optional source HTML |
| `applicantCount`, `applicantText` | Public applicant indicator when displayed |
| `seniorityLevel`, `employmentType` | LinkedIn job criteria |
| `jobFunction`, `industries` | Function and industry labels |
| `salary` | Salary label when displayed on the public search card |
| `benefits` | Search-card badges, for example “Be an early applicant” |
| `companyLogoUrl` | Public company logo image URL |
| `url` | Direct public LinkedIn job URL |
| `searchKeywords`, `searchLocation` | Search context that produced the item |
| `scrapedAt` | UTC collection timestamp |

## Pricing: how much does it cost to scrape LinkedIn jobs?

The Actor uses pay-per-event pricing at **$0.0005 per successfully collected job**: $0.50 per 1,000 jobs. Apify platform compute and proxy costs may also apply according to your plan. Search requests, duplicate IDs, empty pages, and failed detail pages are not charged as job events.

Set a maximum Actor charge in the run options for a hard spending cap. The Actor checks that limit before collecting more results and stops when the available event allowance is exhausted.

## Tips and advanced options

- Start with 25 jobs to confirm that your keyword and location are precise.
- Use `past24Hours` or `pastWeek` for recurring job-monitoring schedules.
- Combine the remote workplace filter with a country location because LinkedIn interprets location context as well as workplace type.
- Disable full descriptions when you only need title, company, location, date, URL, and badges. This reduces requests and is faster.
- Very narrow filters may legitimately return fewer jobs than `maxItems`.
- If a large direct-IP run becomes rate-limited, retry later or enable proxy rotation.

## FAQ, disclaimers, and support

### Does this Actor require a LinkedIn account?

No. It only accesses pages LinkedIn exposes to unauthenticated visitors. It does not accept or store credentials and does not scrape member profiles.

### Why are some fields empty?

LinkedIn and employers do not publish every field for every vacancy. Applicant counts, salary labels, dates, and criteria can be absent. Job postings can also expire between the search and detail requests.

### Is scraping LinkedIn legal?

Web-scraping rules vary by jurisdiction and use case. You are responsible for your inputs, data use, and compliance with applicable law and website terms. Avoid personal-data enrichment, spam, discriminatory screening, or excessive request volumes.

### Is this an official LinkedIn product?

No. This is an unofficial independent Actor with no affiliation with LinkedIn Corporation. LinkedIn is a trademark of its respective owner.

For bugs, selector changes, or feature requests, use the Actor’s **Issues** tab. Custom job-data workflows and output fields can also be discussed there.
