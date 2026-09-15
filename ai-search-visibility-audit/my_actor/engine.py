"""AI search visibility audit engine: crawl, prompt generation, engine sampling, analysis.

All network calls go through ``httpx``.  Every external API is optional: when an
API key is missing the corresponding engine is skipped, and with ``mock=True``
deterministic simulated answers are produced so the pipeline and report can be
validated without spending a cent.
"""

from __future__ import annotations

import hashlib
import html as html_module
import json
import math
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/140.0 Safari/537.36')
KEY_PAGE_HINTS = ('about', 'product', 'pricing', 'plans', 'features', 'solutions', 'services', 'customers',
                  'use-cases', 'blog', 'docs', 'platform', 'why', 'compare', 'vs')
PROMPT_CATEGORIES = [
    ('best_of', 'best-of / recommendation lists ("What are the best tools for X?")'),
    ('comparison', 'direct comparisons and alternatives ("X vs Y", "alternatives to X")'),
    ('how_to', 'how-to and solution-seeking questions where the brand\'s product is a natural answer'),
    ('pricing', 'pricing, cost and value questions'),
    ('trust', 'reviews, reliability, security and "is X legit" questions'),
    ('use_case', 'specific use-case or industry questions the product solves'),
]
URL_PATTERN = re.compile(r'https?://[^\s)\]>"\'`]+', re.I)
Logger = Callable[[str], None]


@dataclass
class SiteProfile:
    url: str
    domain: str
    title: str | None = None
    description: str | None = None
    headings: list[str] = field(default_factory=list)
    pages: list[dict[str, str]] = field(default_factory=list)
    text_excerpt: str = ''

    def as_text(self, limit: int = 6000) -> str:
        parts = [f'Website: {self.url}', f'Title: {self.title or ""}', f'Meta description: {self.description or ""}']
        if self.headings:
            parts.append('Headings: ' + ' | '.join(self.headings[:25]))
        for page in self.pages:
            parts.append(f'Page {page["url"]}: {page.get("title") or ""} — {page.get("text", "")[:700]}')
        if self.text_excerpt:
            parts.append('Homepage text: ' + self.text_excerpt[:1500])
        return '\n'.join(parts)[:limit]


@dataclass
class Sample:
    engine: str
    prompt_id: int
    sample_index: int
    answer: str
    citations: list[str]
    model: str | None = None
    error: str | None = None
    latency_ms: int | None = None


# ----------------------------------------------------------------------- helpers

def domain_of(url: str) -> str:
    host = (urlsplit(url if '://' in url else 'https://' + url).hostname or '').lower()
    return host[4:] if host.startswith('www.') else host


def _clean_text(value: str) -> str:
    value = re.sub(r'<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>', ' ', value, flags=re.S | re.I)
    value = re.sub(r'<[^>]+>', ' ', value)
    return re.sub(r'\s+', ' ', html_module.unescape(value)).strip()


def _meta(page: str, name: str) -> str | None:
    match = re.search(r'<meta[^>]+(?:name|property)=["\']' + re.escape(name) + r'["\'][^>]*content=["\']([^"\']*)["\']', page, re.I)
    if not match:
        match = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:name|property)=["\']' + re.escape(name) + r'["\']', page, re.I)
    return html_module.unescape(match.group(1)).strip() if match else None


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / denom), min(1.0, (centre + margin) / denom)


# ------------------------------------------------------------------------ crawl

def crawl_site(url: str, *, max_pages: int = 5, logger: Logger = print) -> SiteProfile:
    if '://' not in url:
        url = 'https://' + url
    profile = SiteProfile(url=url, domain=domain_of(url))
    client = httpx.Client(headers={'User-Agent': UA, 'Accept-Language': 'en-US,en;q=0.9'}, follow_redirects=True, timeout=25)
    try:
        response = client.get(url)
        response.raise_for_status()
        page = response.text
        title = re.search(r'<title[^>]*>(.*?)</title>', page, re.S | re.I)
        profile.title = _clean_text(title.group(1)) if title else None
        profile.description = _meta(page, 'description') or _meta(page, 'og:description')
        profile.headings = [_clean_text(h) for h in re.findall(r'<h[12][^>]*>(.*?)</h[12]>', page, re.S | re.I)]
        profile.headings = [h for h in profile.headings if h][:30]
        profile.text_excerpt = _clean_text(re.sub(r'<(header|nav|footer)[^>]*>.*?</\1>', ' ', page, flags=re.S | re.I))[:3000]

        links: list[str] = []
        for href in re.findall(r'<a[^>]+href=["\']([^"\'#]+)["\']', page, re.I):
            absolute = urljoin(str(response.url), href)
            if domain_of(absolute) != profile.domain or absolute in links:
                continue
            path = urlsplit(absolute).path.lower()
            if any(hint in path for hint in KEY_PAGE_HINTS):
                links.append(absolute)
        for link in links[: max_pages * 2]:
            if len(profile.pages) >= max_pages:
                break
            try:
                sub = client.get(link)
                if sub.status_code != 200 or 'text/html' not in sub.headers.get('content-type', ''):
                    continue
                sub_title = re.search(r'<title[^>]*>(.*?)</title>', sub.text, re.S | re.I)
                profile.pages.append({
                    'url': str(sub.url),
                    'title': _clean_text(sub_title.group(1)) if sub_title else '',
                    'text': _clean_text(re.sub(r'<(header|nav|footer)[^>]*>.*?</\1>', ' ', sub.text, flags=re.S | re.I))[:1200],
                })
            except httpx.HTTPError as exc:
                logger(f'Skipping {link}: {exc}')
        logger(f'Crawled {profile.url}: title={profile.title!r}, key pages={len(profile.pages)}')
    finally:
        client.close()
    return profile


# ---------------------------------------------------------------- LLM clients

class OpenAIClient:
    def __init__(self, api_key: str, model: str = 'gpt-4o-mini', logger: Logger = print) -> None:
        self.api_key = api_key
        self.model = model
        self.logger = logger
        self._client = httpx.Client(base_url='https://api.openai.com/v1', headers={'Authorization': f'Bearer {api_key}'}, timeout=90)
        self._responses_supported: bool | None = None

    def close(self) -> None:
        self._client.close()

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, temperature: float = 0.7) -> str:
        body: dict[str, Any] = {'model': self.model, 'messages': messages, 'temperature': temperature}
        if json_mode:
            body['response_format'] = {'type': 'json_object'}
        response = self._client.post('/chat/completions', json=body)
        if response.status_code >= 400:
            raise RuntimeError(f'OpenAI chat error {response.status_code}: {response.text[:200]}')
        return response.json()['choices'][0]['message']['content']

    def generate_json(self, system: str, user: str) -> str:
        return self.chat([{'role': 'system', 'content': system}, {'role': 'user', 'content': user}], json_mode=True, temperature=0.8)

    def answer_with_search(self, prompt: str) -> tuple[str, list[str], str]:
        """Ask like a ChatGPT user would; use the Responses API web_search tool when available."""
        if self._responses_supported is not False:
            response = self._client.post('/responses', json={
                'model': self.model,
                'input': prompt,
                'tools': [{'type': 'web_search_preview'}],
            })
            if response.status_code < 400:
                self._responses_supported = True
                data = response.json()
                text_parts: list[str] = []
                citations: list[str] = []
                for item in data.get('output', []):
                    for content in item.get('content', []) or []:
                        if content.get('type') == 'output_text':
                            text_parts.append(content.get('text', ''))
                            for annotation in content.get('annotations', []) or []:
                                if annotation.get('url'):
                                    citations.append(annotation['url'])
                return '\n'.join(text_parts), citations, f'{self.model}+web_search'
            self.logger(f'OpenAI Responses/web_search unavailable ({response.status_code}); falling back to chat completions')
            self._responses_supported = False
        text = self.chat([{'role': 'user', 'content': prompt}])
        return text, [], self.model


class PerplexityClient:
    def __init__(self, api_key: str, model: str = 'sonar') -> None:
        self.model = model
        self._client = httpx.Client(base_url='https://api.perplexity.ai', headers={'Authorization': f'Bearer {api_key}'}, timeout=90)

    def close(self) -> None:
        self._client.close()

    def answer(self, prompt: str) -> tuple[str, list[str], str]:
        response = self._client.post('/chat/completions', json={'model': self.model, 'messages': [{'role': 'user', 'content': prompt}]})
        if response.status_code >= 400:
            raise RuntimeError(f'Perplexity error {response.status_code}: {response.text[:200]}')
        data = response.json()
        text = data['choices'][0]['message']['content']
        citations = [c if isinstance(c, str) else (c.get('url') or '') for c in (data.get('citations') or [])]
        for result in data.get('search_results') or []:
            if isinstance(result, dict) and result.get('url'):
                citations.append(result['url'])
        return text, [c for c in citations if c], self.model


GEMINI_MODEL_FALLBACKS = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-flash-latest']


class GeminiClient:
    """Google AI Studio key. The free tier is enough for a default audit: it is rate-limited per minute,
    so 429s are retried with the delay Google asks for instead of failing the sample."""

    def __init__(self, api_key: str, model: str = 'gemini-2.5-flash', logger: Logger = print) -> None:
        self.model = model
        self.api_key = api_key
        self.logger = logger
        self._client = httpx.Client(base_url='https://generativelanguage.googleapis.com/v1beta', timeout=90)

    def close(self) -> None:
        self._client.close()

    def _generate(self, body: dict[str, Any], *, retries: int = 6) -> dict[str, Any]:
        delay = 10.0
        for attempt in range(1, retries + 1):
            response = self._client.post(f'/models/{self.model}:generateContent', params={'key': self.api_key}, json=body)
            if response.status_code == 404:
                alternatives = [m for m in GEMINI_MODEL_FALLBACKS if m != self.model]
                if not alternatives:
                    raise RuntimeError(f'Gemini model {self.model} not found: {response.text[:200]}')
                self.logger(f'Gemini model {self.model} not available, switching to {alternatives[0]}')
                self.model = alternatives[0]
                continue
            if response.status_code == 429 and attempt < retries:
                wait = delay
                match = re.search(r'retry in (\d+(?:\.\d+)?)s', response.text, flags=re.I)
                if match:
                    wait = min(120.0, float(match.group(1)) + 1.0)
                self.logger(f'Gemini rate limit hit (free tier); waiting {wait:.0f}s (attempt {attempt}/{retries})')
                time.sleep(wait)
                delay = min(120.0, delay * 2)
                continue
            if response.status_code >= 400:
                raise RuntimeError(f'Gemini error {response.status_code}: {response.text[:200]}')
            return response.json()
        raise RuntimeError('Gemini rate limit: retries exhausted')

    @staticmethod
    def _text(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        candidate = (data.get('candidates') or [{}])[0]
        text = ''.join(part.get('text', '') for part in (candidate.get('content') or {}).get('parts', []))
        return text, candidate

    def generate_json(self, system: str, user: str) -> str:
        data = self._generate({
            'systemInstruction': {'parts': [{'text': system}]},
            'contents': [{'parts': [{'text': user}]}],
            'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0.8},
        })
        text, _ = self._text(data)
        return text

    def answer(self, prompt: str) -> tuple[str, list[str], str]:
        data = self._generate({'contents': [{'parts': [{'text': prompt}]}], 'tools': [{'google_search': {}}]})
        text, candidate = self._text(data)
        citations = []
        for chunk in (candidate.get('groundingMetadata') or {}).get('groundingChunks', []) or []:
            uri = (chunk.get('web') or {}).get('uri')
            if uri:
                citations.append(uri)
        return text, citations, f'{self.model}+google_search'


# ---------------------------------------------------------------- prompts

def _fallback_prompts(profile: SiteProfile, brand: str, description: str, count: int) -> list[dict[str, str]]:
    """Template prompts used in mock mode or when the LLM prompt generator is unavailable."""
    topic = (description or profile.description or profile.title or f'{brand} products').strip()
    topic = re.sub(r'\s+', ' ', topic)[:90]
    seeds = [
        ('best_of', f'What are the best tools or services for {topic}?'),
        ('best_of', f'Top 5 companies I should consider for {topic}'),
        ('comparison', f'What are good alternatives to {brand}?'),
        ('comparison', f'How does {brand} compare with its main competitors?'),
        ('how_to', f'How do I get started with {topic}? Which provider would you recommend?'),
        ('how_to', f'I need a reliable solution for {topic}. What should I use?'),
        ('pricing', f'How much does it cost to use a service for {topic}, and which one is best value?'),
        ('pricing', f'Is {brand} worth the price compared to alternatives?'),
        ('trust', f'Is {brand} a legitimate and trustworthy company?'),
        ('trust', f'Which providers for {topic} have the best reviews?'),
        ('use_case', f'Recommend a provider for {topic} for a small business'),
        ('use_case', f'Which {topic} solution do enterprises use?'),
    ]
    prompts: list[dict[str, str]] = []
    index = 0
    while len(prompts) < count:
        category, text = seeds[index % len(seeds)]
        suffix = '' if index < len(seeds) else f' (variant {index // len(seeds) + 1})'
        prompts.append({'category': category, 'prompt': text + suffix})
        index += 1
    return prompts


def generate_prompts(profile: SiteProfile, brand: str, competitors: list[str], description: str, count: int,
                     generator: Any | None, logger: Logger = print) -> list[dict[str, str]]:
    """`generator` is an OpenAIClient or GeminiClient (anything with generate_json); None -> templates."""
    if generator is None:
        return _fallback_prompts(profile, brand, description, count)
    categories = '\n'.join(f'- {key}: {label}' for key, label in PROMPT_CATEGORIES)
    system = ('You design realistic questions that potential buyers type into AI assistants such as ChatGPT, '
              'Perplexity or Gemini before choosing a product or vendor. Questions must be natural, specific to the '
              'market described, and must NOT mention the audited brand by name unless the category is "comparison" '
              'or "trust" (at most a third of those may name it). Return JSON only.')
    user = (f'Audited brand: {brand}\nCompetitors: {", ".join(competitors) or "unknown"}\n'
            f'Extra description: {description or "(none)"}\n\nSite profile:\n{profile.as_text()}\n\n'
            f'Generate exactly {count} questions spread evenly across these categories:\n{categories}\n\n'
            'Respond as {"prompts": [{"category": "<key>", "prompt": "<question>"}, ...]}.')
    try:
        raw = generator.generate_json(system, user)
        data = json.loads(raw)
        prompts = [p for p in data.get('prompts', []) if isinstance(p, dict) and p.get('prompt')]
        valid = {key for key, _ in PROMPT_CATEGORIES}
        for p in prompts:
            if p.get('category') not in valid:
                p['category'] = 'use_case'
        if len(prompts) >= max(5, count // 2):
            return prompts[:count]
        logger(f'Prompt generator returned only {len(prompts)} prompts; topping up with templates')
        return (prompts + _fallback_prompts(profile, brand, description, count))[:count]
    except Exception as exc:  # noqa: BLE001
        logger(f'Prompt generation failed ({exc}); using template prompts')
        return _fallback_prompts(profile, brand, description, count)


# ---------------------------------------------------------------- mock engine

def mock_answer(engine: str, prompt: dict[str, str], sample_index: int, brand: str, domain: str, competitors: list[str]) -> tuple[str, list[str]]:
    seed = int(hashlib.sha256(f'{engine}|{prompt["prompt"]}|{sample_index}'.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    pool = [brand] + competitors + ['a well-known open-source option']
    rng.shuffle(pool)
    mentioned = pool[: rng.randint(1, min(4, len(pool)))]
    if prompt['category'] in ('comparison', 'trust') and brand not in mentioned and rng.random() < 0.6:
        mentioned.insert(0, brand)
    lines = [f'Here are some options for your question ({prompt["category"].replace("_", " ")}):']
    citations: list[str] = []
    for name in mentioned:
        lines.append(f'- {name}: a popular choice with solid documentation and pricing that scales with usage.')
        if name == brand and rng.random() < 0.7:
            citations.append(f'https://{domain}/')
        elif name in competitors and rng.random() < 0.5:
            citations.append(f'https://{name.lower().replace(" ", "")}.com/')
    for extra in ('https://www.g2.com/', 'https://www.reddit.com/', 'https://medium.com/', 'https://news.ycombinator.com/'):
        if rng.random() < 0.35:
            citations.append(extra)
    return '\n'.join(lines), citations


# ---------------------------------------------------------------- analysis

def _mention_regex(names: list[str]) -> re.Pattern[str] | None:
    names = [n for n in names if n and n.strip()]
    if not names:
        return None
    return re.compile(r'(?<![A-Za-z0-9])(' + '|'.join(re.escape(n.strip()) for n in names) + r')(?![A-Za-z0-9])', re.I)


def analyse_sample(sample: Sample, *, brand: str, aliases: list[str], domain: str, competitors: list[str]) -> dict[str, Any]:
    text = sample.answer or ''
    brand_re = _mention_regex([brand, *aliases, domain])
    brand_mentioned = bool(brand_re and brand_re.search(text))
    urls = URL_PATTERN.findall(text) + list(sample.citations)
    cited_domains = sorted({domain_of(u) for u in urls if domain_of(u)})
    brand_cited = domain in cited_domains
    competitors_mentioned = []
    first_positions: dict[str, int] = {}
    for competitor in competitors:
        regex = _mention_regex([competitor])
        match = regex.search(text) if regex else None
        if match:
            competitors_mentioned.append(competitor)
            first_positions[competitor] = match.start()
    if brand_mentioned and brand_re:
        first_positions[brand] = brand_re.search(text).start()  # type: ignore[union-attr]
    ranking = [name for name, _ in sorted(first_positions.items(), key=lambda kv: kv[1])]
    brand_rank = ranking.index(brand) + 1 if brand in ranking else None
    sentences = [s.strip() for s in re.split(r'(?<=[.!?\n])\s+', text) if brand_re and brand_re.search(s)] if brand_mentioned else []
    return {
        'brandMentioned': brand_mentioned,
        'brandCited': brand_cited,
        'brandRank': brand_rank,
        'competitorsMentioned': competitors_mentioned,
        'citedDomains': cited_domains,
        'brandSentences': sentences[:5],
    }


def summarise(results: list[dict[str, Any]], *, brand: str, competitors: list[str], engines: list[str]) -> dict[str, Any]:
    def rate(items: list[dict[str, Any]], key: str) -> dict[str, Any]:
        n = len(items)
        k = sum(1 for r in items if r.get(key))
        low, high = wilson_interval(k, n)
        return {'count': k, 'n': n, 'rate': round(k / n, 4) if n else None, 'ci95': [round(low, 4), round(high, 4)]}

    ok = [r for r in results if not r.get('error')]
    summary: dict[str, Any] = {
        'samples': len(results),
        'samplesOk': len(ok),
        'brandMentionRate': rate(ok, 'brandMentioned'),
        'brandCitationRate': rate(ok, 'brandCited'),
        'byEngine': {},
        'byCategory': {},
        'shareOfVoice': {},
        'topCitedDomains': [],
        'averageBrandRank': None,
    }
    for engine in engines:
        items = [r for r in ok if r['engine'] == engine]
        summary['byEngine'][engine] = {'mention': rate(items, 'brandMentioned'), 'cited': rate(items, 'brandCited')}
    for category, _ in PROMPT_CATEGORIES:
        items = [r for r in ok if r['category'] == category]
        if items:
            summary['byCategory'][category] = {'mention': rate(items, 'brandMentioned'), 'cited': rate(items, 'brandCited')}
    voice: dict[str, int] = {brand: sum(1 for r in ok if r['brandMentioned'])}
    for competitor in competitors:
        voice[competitor] = sum(1 for r in ok if competitor in r['competitorsMentioned'])
    total_voice = sum(voice.values()) or 1
    summary['shareOfVoice'] = {name: {'mentions': count, 'share': round(count / total_voice, 4)} for name, count in sorted(voice.items(), key=lambda kv: -kv[1])}
    domain_counts: dict[str, int] = {}
    for r in ok:
        for d in r['citedDomains']:
            domain_counts[d] = domain_counts.get(d, 0) + 1
    summary['topCitedDomains'] = [{'domain': d, 'count': c} for d, c in sorted(domain_counts.items(), key=lambda kv: -kv[1])[:15]]
    ranks = [r['brandRank'] for r in ok if r.get('brandRank')]
    summary['averageBrandRank'] = round(sum(ranks) / len(ranks), 2) if ranks else None
    return summary


# ---------------------------------------------------------------- pipeline

def run_audit(
    *,
    website_url: str,
    brand: str,
    aliases: list[str],
    competitors: list[str],
    description: str,
    prompt_count: int,
    samples_per_prompt: int,
    engines: list[str],
    keys: dict[str, str],
    openai_model: str,
    mock: bool,
    gemini_model: str = 'gemini-2.5-flash',
    logger: Logger = print,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    started = time.time()
    profile = crawl_site(website_url, logger=logger)
    openai = OpenAIClient(keys['openai'], openai_model, logger) if keys.get('openai') and not mock else None
    gemini = GeminiClient(keys['gemini'], gemini_model, logger) if keys.get('gemini') and not mock else None
    generator = openai or gemini
    prompts = generate_prompts(profile, brand, competitors, description, prompt_count, generator, logger)
    for index, prompt in enumerate(prompts, start=1):
        prompt['id'] = index
    source = 'template' if generator is None else ('OpenAI-generated' if generator is openai else 'Gemini-generated')
    logger(f'{len(prompts)} prompts ready ({source})')

    clients: dict[str, Any] = {}
    if not mock:
        if 'openai' in engines and openai:
            clients['openai'] = openai
        if 'perplexity' in engines and keys.get('perplexity'):
            clients['perplexity'] = PerplexityClient(keys['perplexity'])
        if 'gemini' in engines and gemini:
            clients['gemini'] = gemini
    active_engines = engines if mock else [e for e in engines if e in clients]
    skipped = [e for e in engines if e not in active_engines]
    if skipped:
        logger(f'Engines skipped (no API key): {", ".join(skipped)}')

    results: list[dict[str, Any]] = []
    total = len(prompts) * samples_per_prompt * len(active_engines)
    done = 0
    for engine in active_engines:
        for prompt in prompts:
            for sample_index in range(samples_per_prompt):
                t0 = time.time()
                error = None
                model = 'mock'
                citations: list[str] = []
                try:
                    if mock:
                        answer, citations = mock_answer(engine, prompt, sample_index, brand, profile.domain, competitors)
                    elif engine == 'openai':
                        answer, citations, model = clients['openai'].answer_with_search(prompt['prompt'])
                    elif engine == 'perplexity':
                        answer, citations, model = clients['perplexity'].answer(prompt['prompt'])
                    else:
                        answer, citations, model = clients['gemini'].answer(prompt['prompt'])
                except Exception as exc:  # noqa: BLE001
                    answer, error = '', f'{type(exc).__name__}: {exc}'[:300]
                    logger(f'{engine} prompt {prompt["id"]} sample {sample_index + 1} failed: {error}')
                sample = Sample(engine, prompt['id'], sample_index, answer, citations, model, error, int((time.time() - t0) * 1000))
                analysis = analyse_sample(sample, brand=brand, aliases=aliases, domain=profile.domain, competitors=competitors)
                results.append({
                    'type': 'promptResult', 'engine': engine, 'model': model, 'promptId': prompt['id'],
                    'category': prompt['category'], 'prompt': prompt['prompt'], 'sampleIndex': sample_index + 1,
                    'answer': answer[:4000], 'citations': citations, 'error': error, 'latencyMs': sample.latency_ms,
                    'mode': 'mock' if mock else 'live', **analysis,
                })
                done += 1
                if progress and (done % 10 == 0 or done == total):
                    progress({'done': done, 'total': total})
                if not mock:
                    time.sleep(0.3)
    for client in clients.values():
        client.close()

    summary = summarise(results, brand=brand, competitors=competitors, engines=active_engines)
    return {
        'brand': brand, 'website': profile.url, 'domain': profile.domain, 'competitors': competitors,
        'engines': active_engines, 'enginesSkipped': skipped, 'mode': 'mock' if mock else 'live',
        'promptCount': len(prompts), 'samplesPerPrompt': samples_per_prompt,
        'siteProfile': {'title': profile.title, 'description': profile.description, 'keyPages': [p['url'] for p in profile.pages]},
        'prompts': prompts, 'results': results, 'summary': summary,
        'durationSeconds': round(time.time() - started, 1),
    }
