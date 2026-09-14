# AI Search Visibility Audit: ChatGPT & Perplexity

> **Run it on Apify Store:** https://apify.com/kyungminlee/ai-search-visibility-audit — pay per result, no setup. This folder is the Actor's source code (Python, `httpx`, Apify SDK).

Find out **how often AI answer engines recommend your brand** when buyers ask
them for advice — and who they recommend instead. The Actor reads your website,
writes 30 realistic buyer-intent questions, asks ChatGPT, Perplexity and/or
Gemini each of them several times, and delivers a shareable **HTML report**
with your brand's mention rate, citation rate, share of voice against
competitors, the sources the engines rely on, and every sentence they wrote
about you. This is an unofficial tool and is not affiliated with OpenAI,
Perplexity, or Google.

## What does the audit measure?

- **Mention rate** – share of AI answers that name your brand (or an alias or
  your domain), with a 95% confidence interval.
- **Citation rate** – share of answers whose sources include your website.
- **Average position** – where your brand appears among the brands named.
- **Share of voice** – your mentions versus each listed competitor.
- **Most cited sources** – the domains AI engines trust for your market (the
  places where you need to be present).
- **What the engines say** – every sentence about your brand, so you can catch
  outdated or wrong claims.
- **By engine and by question type** – best-of lists, comparisons, how-to,
  pricing, trust, and use-case questions are reported separately.

## How it works

1. **Crawl** – the homepage plus up to five key pages (about, products,
   pricing, blog) are read to understand what you sell and to whom.
2. **Prompt generation** – an LLM turns that profile into buyer-intent questions
   spread across six categories. Questions do not name your brand except in
   comparison / trust categories, so results reflect unprompted recall.
3. **Sampling** – every question is asked `samplesPerPrompt` times per engine
   (default 3), because AI answers vary from run to run. ChatGPT is queried
   through the OpenAI Responses API with web search when available, Perplexity
   through `sonar`, Gemini with Google Search grounding.
4. **Analysis** – mentions, citations, ranks, competitor mentions and cited
   domains are extracted from every answer; rates get Wilson 95% intervals.
5. **Report** – `REPORT.html` in the run's key-value store, plus one `audit`
   summary item and one `promptResult` item per answer in the dataset.

## How to use it

1. Enter your **Website URL** and **Brand name**; add **Competitors** to
   measure share of voice.
2. Paste an **OpenAI API key** (needed for prompt generation and the ChatGPT
   engine). Optionally add Perplexity and Gemini keys and select those engines.
3. Keep 30 prompts × 3 samples for a first audit (about 90 answers per engine;
   typically under $1 of OpenAI usage with `gpt-4o-mini`).
4. Click **Start**. Open `REPORT.html` from the **Storage → Key-value store**
   tab or via the link in the `audit` dataset item.

No API key? Enable **Mock mode** to see the full report format with simulated
answers — mock runs are free and clearly labelled.

## Input

| Field | Default | Notes |
|---|---|---|
| `websiteUrl` | – | Brand homepage (required). |
| `brandName` | – | Brand as written in text (required). |
| `brandAliases` | – | Extra spellings or product names counted as mentions. |
| `competitors` | – | Up to 10 competitor names. |
| `businessDescription` | – | Optional context to improve prompt generation. |
| `promptCount` | 30 | 5–60 generated questions. |
| `samplesPerPrompt` | 3 | 1–5 answers per question per engine. |
| `engines` | `["openai"]` | `openai`, `perplexity`, `gemini`. |
| `openaiApiKey` / `openaiModel` | – / `gpt-4o-mini` | Prompt generation + ChatGPT engine. |
| `perplexityApiKey`, `geminiApiKey` | – | Enable the other engines. |
| `mockMode` | `false` | Simulated answers, free of charge. |

## Output

`audit` item (one per run):

```json
{
  "type": "audit",
  "brand": "Apify",
  "engines": ["openai", "perplexity"],
  "promptCount": 30,
  "brandMentionRate": {"count": 49, "n": 180, "rate": 0.2722, "ci95": [0.2115, 0.3426]},
  "brandCitationRate": {"count": 31, "n": 180, "rate": 0.1722, "ci95": [0.1236, 0.2348]},
  "averageBrandRank": 1.6,
  "shareOfVoice": {"Apify": {"mentions": 49, "share": 0.31}, "Bright Data": {"mentions": 44, "share": 0.28}},
  "topCitedDomains": [{"domain": "g2.com", "count": 38}, {"domain": "apify.com", "count": 31}],
  "reportUrl": "https://api.apify.com/v2/key-value-stores/.../records/REPORT.html"
}
```

`promptResult` items (one per answer): `engine`, `model`, `promptId`,
`category`, `prompt`, `sampleIndex`, `answer`, `citations`, `brandMentioned`,
`brandCited`, `brandRank`, `competitorsMentioned`, `citedDomains`,
`brandSentences`, `error`, `latencyMs`, `mode`.

## Pricing

One `audit` event per completed live audit (the report and dataset are
delivered first; a run that fails before the report is written is not
charged). Mock runs are free. Your own OpenAI / Perplexity / Gemini API usage is
billed by those providers separately and is typically well under $1 per audit
with the default settings.

## Tips

- Run the audit monthly and compare `brandMentionRate` over time; the
  confidence interval tells you whether a change is real.
- Look at **Most cited sources** first: getting listed on those domains is the
  fastest way to raise your citation rate.
- Use `businessDescription` when your homepage is thin or JavaScript-only.
- Keep prompts at 30 and samples at 3 for comparable results across runs.

## FAQ

**Do I need to give you my API keys?** Keys are entered as secret input
fields, used only during the run, and never stored in the dataset or report.

**Why do the numbers differ between runs?** AI answers are non-deterministic;
that is exactly why each prompt is sampled several times and rates carry
confidence intervals. Larger samples narrow the interval.

**Which models are used?** OpenAI `gpt-4o-mini` by default (configurable),
Perplexity `sonar`, Gemini `gemini-2.0-flash` with Google Search grounding.

**Can it fix my visibility?** It measures and points to the sources that
matter; content and PR work is up to you.
