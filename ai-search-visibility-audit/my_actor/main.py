"""Apify Actor entry point for the AI search visibility audit."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from apify import Actor

from .engine import run_audit
from .report import render_report

EVENT_AUDIT = 'audit'


def _strings(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [str(v).strip() for v in values if str(v or '').strip()]


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        website = str(inp.get('websiteUrl') or '').strip()
        brand = str(inp.get('brandName') or '').strip()
        if not website or not brand:
            raise ValueError('websiteUrl and brandName are required.')
        aliases = _strings(inp.get('brandAliases'))
        competitors = _strings(inp.get('competitors'))[:10]
        description = str(inp.get('businessDescription') or '').strip()
        prompt_count = max(5, min(60, int(inp.get('promptCount') or 30)))
        samples = max(1, min(5, int(inp.get('samplesPerPrompt') or 3)))
        engines = [e for e in _strings(inp.get('engines')) if e in ('openai', 'perplexity', 'gemini')] or ['openai']
        keys = {
            'openai': str(inp.get('openaiApiKey') or '').strip(),
            'perplexity': str(inp.get('perplexityApiKey') or '').strip(),
            'gemini': str(inp.get('geminiApiKey') or '').strip(),
        }
        mock = bool(inp.get('mockMode'))
        if not mock and not any(keys[e] for e in engines):
            Actor.log.warning('No API key was provided for the selected engines - running in MOCK mode (simulated answers, not charged).')
            mock = True

        # Mock runs are free; only a live audit is worth an `audit` event, and only once the report exists.
        if not mock:
            try:
                allowed = Actor.get_charging_manager().calculate_max_event_charge_count_within_limit(EVENT_AUDIT)
            except Exception:  # noqa: BLE001
                allowed = None
            if allowed is not None and allowed < 1:
                raise RuntimeError('The run\'s maximum charge is below the price of one audit; raise the limit and retry.')

        def progress(state: dict[str, Any]) -> None:
            Actor.log.info(f"Sampling {state['done']}/{state['total']} answers")

        audit = await asyncio.to_thread(
            run_audit,
            website_url=website, brand=brand, aliases=aliases, competitors=competitors, description=description,
            prompt_count=prompt_count, samples_per_prompt=samples, engines=engines, keys=keys,
            openai_model=str(inp.get('openaiModel') or 'gpt-4o-mini'), mock=mock,
            gemini_model=str(inp.get('geminiModel') or 'gemini-2.5-flash'),
            logger=lambda m: Actor.log.info(m), progress=progress,
        )
        if not audit['engines']:
            failed = audit.get('enginesFailed') or []
            if failed:
                first_error = next((r['error'] for r in audit['results'] if r.get('error')), 'unknown error')
                raise RuntimeError(f'Every answer from {", ".join(failed)} failed ({first_error}). Nothing was charged; check the API key and model, then retry.')
            raise RuntimeError('No engine could be sampled. Provide an API key for at least one selected engine, or enable mockMode.')

        report_html = render_report(audit)
        await Actor.set_value('REPORT.html', report_html, content_type='text/html; charset=utf-8')
        await Actor.set_value('AUDIT.json', {k: v for k, v in audit.items() if k != 'results'})

        store = await Actor.open_key_value_store()
        report_url = await store.get_public_url('REPORT.html')
        summary_item = {
            'type': 'audit', 'brand': brand, 'website': audit['website'], 'domain': audit['domain'], 'mode': audit['mode'],
            'engines': audit['engines'], 'enginesSkipped': audit['enginesSkipped'], 'enginesFailed': audit.get('enginesFailed', []), 'promptCount': audit['promptCount'],
            'samplesPerPrompt': samples, 'reportUrl': report_url, 'generatedAt': datetime.now(UTC).isoformat(),
            **audit['summary'],
        }
        charged = False
        if not mock:
            charge = await Actor.charge(EVENT_AUDIT, count=1)
            charged = bool(charge.charged_count) if hasattr(charge, 'charged_count') else True
        summary_item['charged'] = charged
        await Actor.push_data(summary_item)
        for result in audit['results']:
            await Actor.push_data(result)

        s = audit['summary']
        Actor.log.info(
            f"Audit done in {audit['durationSeconds']}s: mention rate {s['brandMentionRate']['rate']}, "
            f"citation rate {s['brandCitationRate']['rate']}, {s['samplesOk']}/{s['samples']} answers OK. Report: {report_url}"
        )


if __name__ == '__main__':
    asyncio.run(main())
