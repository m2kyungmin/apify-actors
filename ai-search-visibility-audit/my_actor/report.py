"""Render the audit result as a self-contained HTML report (no external assets)."""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any

from .engine import PROMPT_CATEGORIES

CSS = """
body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:0;background:#f6f7f9;color:#1b1f24}
.wrap{max-width:980px;margin:0 auto;padding:32px 20px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:20px;margin:36px 0 12px;border-bottom:2px solid #e3e6ea;padding-bottom:6px}
.muted{color:#5b6570}.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:12px;background:#e8f0fe;color:#1a56db;margin-left:6px}
.badge.mock{background:#fff4e5;color:#9a5b00}
.tiles{display:flex;flex-wrap:wrap;gap:14px;margin-top:16px}
.tile{flex:1 1 200px;background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:16px}
.tile .v{font-size:32px;font-weight:700}.tile .l{font-size:13px;color:#5b6570}.tile .ci{font-size:12px;color:#8a949e}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e3e6ea;border-radius:8px;overflow:hidden;font-size:14px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #eef0f3;vertical-align:top}th{background:#f0f2f5;font-weight:600}
.bar{height:10px;background:#e3e6ea;border-radius:5px;overflow:hidden}.bar i{display:block;height:100%;background:#1a56db}
.ok{color:#137333;font-weight:600}.no{color:#b3261e;font-weight:600}
details{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:8px 12px;margin:8px 0}summary{cursor:pointer;font-weight:600}
pre{white-space:pre-wrap;font-size:13px;background:#f8f9fb;padding:10px;border-radius:6px}
.foot{margin-top:40px;font-size:12px;color:#8a949e}
"""


def _pct(rate: dict[str, Any]) -> str:
    if rate.get('rate') is None:
        return 'n/a'
    return f"{rate['rate'] * 100:.0f}%"


def _ci(rate: dict[str, Any]) -> str:
    if rate.get('rate') is None:
        return ''
    low, high = rate['ci95']
    return f"95% CI {low * 100:.0f}–{high * 100:.0f}% · n={rate['n']}"


def render_report(audit: dict[str, Any]) -> str:
    e = html.escape
    s = audit['summary']
    brand = audit['brand']
    mode_badge = '<span class="badge mock">MOCK DATA — demo only</span>' if audit['mode'] == 'mock' else '<span class="badge">live sampling</span>'
    labels = dict(PROMPT_CATEGORIES)

    engine_rows = ''.join(
        f"<tr><td>{e(engine)}</td><td>{_pct(v['mention'])} <span class='muted'>({_ci(v['mention'])})</span></td>"
        f"<td>{_pct(v['cited'])} <span class='muted'>({_ci(v['cited'])})</span></td></tr>"
        for engine, v in s['byEngine'].items()
    )
    category_rows = ''.join(
        f"<tr><td>{e(labels.get(cat, cat).split(' (')[0])}</td><td>{_pct(v['mention'])}</td><td>{_pct(v['cited'])}</td><td class='muted'>n={v['mention']['n']}</td></tr>"
        for cat, v in s['byCategory'].items()
    )
    max_share = max((v['share'] for v in s['shareOfVoice'].values()), default=0) or 1
    voice_rows = ''.join(
        f"<tr><td>{'<b>' + e(name) + '</b>' if name == brand else e(name)}</td><td>{v['mentions']}</td>"
        f"<td style='width:45%'><div class='bar'><i style='width:{v['share'] / max_share * 100:.0f}%'></i></div></td><td>{v['share'] * 100:.0f}%</td></tr>"
        for name, v in s['shareOfVoice'].items()
    )
    domain_rows = ''.join(
        f"<tr><td>{'<b>' + e(d['domain']) + '</b>' if d['domain'] == audit['domain'] else e(d['domain'])}</td><td>{d['count']}</td></tr>"
        for d in s['topCitedDomains']
    ) or '<tr><td colspan="2" class="muted">No sources were cited by the sampled answers.</td></tr>'

    prompt_blocks = []
    for prompt in audit['prompts']:
        rows = [r for r in audit['results'] if r['promptId'] == prompt['id']]
        mentions = sum(1 for r in rows if r['brandMentioned'])
        cites = sum(1 for r in rows if r['brandCited'])
        samples_html = ''.join(
            f"<details><summary>{e(r['engine'])} · sample {r['sampleIndex']} · "
            f"{'<span class=ok>brand mentioned</span>' if r['brandMentioned'] else '<span class=no>not mentioned</span>'}"
            f"{' · cited' if r['brandCited'] else ''}{' · rank ' + str(r['brandRank']) if r.get('brandRank') else ''}"
            f"{' · <span class=no>error</span>' if r.get('error') else ''}</summary>"
            f"<pre>{e(r['answer'] or r.get('error') or '')}</pre>"
            + (f"<div class='muted'>Sources: {e(', '.join(r['citedDomains']))}</div>" if r['citedDomains'] else '')
            + '</details>'
            for r in rows
        )
        prompt_blocks.append(
            f"<details><summary>#{prompt['id']} [{e(labels.get(prompt['category'], prompt['category']).split(' (')[0])}] "
            f"{e(prompt['prompt'])} — mentioned {mentions}/{len(rows)}, cited {cites}/{len(rows)}</summary>{samples_html}</details>"
        )

    brand_sentences = []
    for r in audit['results']:
        for sentence in r.get('brandSentences') or []:
            if sentence not in brand_sentences:
                brand_sentences.append(sentence)
    claims_html = ''.join(f'<li>{e(x)}</li>' for x in brand_sentences[:40]) or '<li class="muted">The brand was not described in any sampled answer.</li>'

    generated = datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Search Visibility Audit — {e(brand)}</title><style>{CSS}</style></head><body><div class="wrap">
<h1>AI Search Visibility Audit: {e(brand)} {mode_badge}</h1>
<div class="muted">{e(audit['website'])} · generated {generated} · engines: {e(', '.join(audit['engines']) or 'none')} · {audit['promptCount']} prompts × {audit['samplesPerPrompt']} samples</div>
<div class="tiles">
<div class="tile"><div class="v">{_pct(s['brandMentionRate'])}</div><div class="l">of AI answers mention {e(brand)}</div><div class="ci">{_ci(s['brandMentionRate'])}</div></div>
<div class="tile"><div class="v">{_pct(s['brandCitationRate'])}</div><div class="l">of answers cite {e(audit['domain'])} as a source</div><div class="ci">{_ci(s['brandCitationRate'])}</div></div>
<div class="tile"><div class="v">{s['averageBrandRank'] if s['averageBrandRank'] else '–'}</div><div class="l">average position of {e(brand)} among brands named</div><div class="ci">1 = named first</div></div>
<div class="tile"><div class="v">{s['samplesOk']}</div><div class="l">answers analysed</div><div class="ci">{s['samples'] - s['samplesOk']} failed calls excluded{(' · stopped early: ' + e(', '.join(audit['enginesStoppedEarly']))) if audit.get('enginesStoppedEarly') else ''}</div></div>
</div>
<h2>By engine</h2><table><tr><th>Engine</th><th>Brand mentioned</th><th>Brand site cited</th></tr>{engine_rows}</table>
<h2>By question type</h2><table><tr><th>Category</th><th>Mentioned</th><th>Cited</th><th></th></tr>{category_rows}</table>
<h2>Share of voice</h2><table><tr><th>Brand</th><th>Mentions</th><th></th><th>Share</th></tr>{voice_rows}</table>
<h2>Most cited sources</h2><table><tr><th>Domain</th><th>Citations</th></tr>{domain_rows}</table>
<h2>What the engines say about {e(brand)}</h2><p class="muted">Sentences that name the brand, for fact-checking by the brand owner.</p><ul>{claims_html}</ul>
<h2>All prompts and answers</h2>{''.join(prompt_blocks)}
<h2>Methodology</h2>
{('<p class="muted"><strong>Note:</strong> Gemini answers were generated without Google Search grounding because the key\'s grounding quota was exhausted; citation figures for Gemini therefore come only from links written into the answer text.</p>' if any(str(r.get('model', '')).endswith('+no_search') for r in audit['results']) else '')}
<p>The Actor read the brand's homepage and {len(audit['siteProfile']['keyPages'])} key pages, generated {audit['promptCount']} buyer-intent questions across six categories (best-of, comparison, how-to, pricing, trust, use case), and asked each question {audit['samplesPerPrompt']} time(s) per engine because AI answers are non-deterministic. A response counts as a <em>mention</em> when it names the brand, an alias, or its domain; it counts as a <em>citation</em> when the engine's returned sources or in-text links include the brand domain. Rates are shown with Wilson 95% confidence intervals; with {s['samplesOk']} answers the interval width is the honest measure of uncertainty. Share of voice divides mentions among the audited brand and the listed competitors only. Results describe the sampled engines and models on the generation date and change over time.</p>
{"<p class='muted'><b>Mock mode:</b> answers above were simulated to demonstrate the report format; run with API keys for real measurements.</p>" if audit['mode'] == 'mock' else ''}
<div class="foot">Generated by AI Search Visibility Audit on Apify. Unofficial; not affiliated with OpenAI, Perplexity or Google.</div>
</div></body></html>"""
