"""Client and parsers for LinkedIn's public guest jobs HTML endpoints."""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Tag

SEARCH_URL = 'https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search'
DETAIL_URL = 'https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}'
DEFAULT_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Cache-Control': 'no-cache',
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36'
    ),
}
DATE_POSTED = {'past24Hours': 'r86400', 'pastWeek': 'r604800', 'pastMonth': 'r2592000'}
JOB_TYPES = {
    'fullTime': 'F', 'partTime': 'P', 'contract': 'C', 'temporary': 'T',
    'volunteer': 'V', 'internship': 'I', 'other': 'O',
}
EXPERIENCE_LEVELS = {
    'internship': '1', 'entryLevel': '2', 'associate': '3',
    'midSenior': '4', 'director': '5', 'executive': '6',
}
WORKPLACE_TYPES = {'onSite': '1', 'remote': '2', 'hybrid': '3'}


class LinkedInError(RuntimeError):
    """Base error for the guest jobs client."""


class LinkedInBlocked(LinkedInError):
    """LinkedIn rate-limited or blocked a request."""


@dataclass(slots=True)
class SearchOptions:
    keywords: str
    location: str = ''
    date_posted: str = 'anyTime'
    job_types: list[str] = field(default_factory=list)
    experience_levels: list[str] = field(default_factory=list)
    workplace_types: list[str] = field(default_factory=list)
    easy_apply_only: bool = False
    sort_by: str = 'relevance'

    def params(self, start: int) -> dict[str, str | int]:
        if not self.keywords.strip():
            raise ValueError('keywords must not be empty')
        params: dict[str, str | int] = {'keywords': self.keywords.strip(), 'start': start}
        if self.location.strip():
            params['location'] = self.location.strip()
        if self.date_posted in DATE_POSTED:
            params['f_TPR'] = DATE_POSTED[self.date_posted]
        if self.job_types:
            params['f_JT'] = ','.join(_map_values(self.job_types, JOB_TYPES, 'job type'))
        if self.experience_levels:
            params['f_E'] = ','.join(_map_values(self.experience_levels, EXPERIENCE_LEVELS, 'experience level'))
        if self.workplace_types:
            params['f_WT'] = ','.join(_map_values(self.workplace_types, WORKPLACE_TYPES, 'workplace type'))
        if self.easy_apply_only:
            params['f_AL'] = 'true'
        if self.sort_by == 'mostRecent':
            params['sortBy'] = 'DD'
        return params


def _map_values(values: Iterable[str], mapping: dict[str, str], label: str) -> list[str]:
    output: list[str] = []
    for value in values:
        if value not in mapping:
            raise ValueError(f'Unknown {label}: {value}')
        output.append(mapping[value])
    return output


def _text(node: Tag | None, separator: str = ' ') -> str | None:
    if node is None:
        return None
    value = re.sub(r'\s+', ' ', node.get_text(separator, strip=True)).strip()
    return value or None


def _clean_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/'), '', ''))


def _job_id(node: Tag) -> str | None:
    match = re.search(r'jobPosting:(\d+)', str(node.get('data-entity-urn', '')))
    if match:
        return match.group(1)
    link = node.select_one('a.base-card__full-link')
    match = re.search(r'-(\d+)(?:[/?]|$)', str(link.get('href', '')) if link else '')
    return match.group(1) if match else None


def parse_search_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, 'lxml')
    jobs: list[dict] = []
    for card in soup.select('.base-search-card'):
        job_id = _job_id(card)
        title = _text(card.select_one('.base-search-card__title'))
        company = _text(card.select_one('.base-search-card__subtitle'))
        if not job_id or not title or not company:
            continue
        link = card.select_one('a.base-card__full-link')
        company_link = card.select_one('.base-search-card__subtitle a')
        date = card.select_one('time')
        logo = card.select_one('img')
        benefits = [_text(node) for node in card.select('.job-posting-benefits__text')]
        jobs.append({
            'jobId': job_id,
            'title': title,
            'companyName': company,
            'companyUrl': _clean_url(str(company_link.get('href', ''))) if company_link else None,
            'location': _text(card.select_one('.job-search-card__location')),
            'postedDate': str(date.get('datetime')) if date and date.get('datetime') else None,
            'postedTime': _text(date),
            'salary': _text(card.select_one('.job-search-card__salary-info')),
            'benefits': [value for value in benefits if value],
            'companyLogoUrl': str(logo.get('data-delayed-url') or logo.get('src')) if logo else None,
            'url': _clean_url(str(link.get('href', ''))) if link else f'https://www.linkedin.com/jobs/view/{job_id}',
        })
    return jobs


def parse_detail_html(html: str, job_id: str) -> dict:
    soup = BeautifulSoup(html, 'lxml')
    description = soup.select_one('.show-more-less-html__markup')
    if description is None:
        raise LinkedInError(f'Job {job_id}: public description not found')

    criteria: dict[str, str] = {}
    for row in soup.select('.description__job-criteria-item'):
        key = _text(row.select_one('.description__job-criteria-subheader'))
        value = _text(row.select_one('.description__job-criteria-text'))
        if key and value:
            criteria[key.lower()] = value

    applicant_text = _text(soup.select_one('.num-applicants__caption'))
    applicant_count = None
    if applicant_text and (match := re.search(r'([\d,]+)', applicant_text)):
        applicant_count = int(match.group(1).replace(',', ''))

    company_link = soup.select_one('.topcard__org-name-link')
    logo = soup.select_one('.top-card-layout img')
    detail = {
        'title': _text(soup.select_one('.topcard__title')),
        'companyName': _text(company_link),
        'companyUrl': _clean_url(str(company_link.get('href', ''))) if company_link else None,
        'companyLogoUrl': str(logo.get('data-delayed-url') or logo.get('src')) if logo else None,
        'postedTime': _text(soup.select_one('.posted-time-ago__text')),
        'applicantCount': applicant_count,
        'applicantText': applicant_text,
        'descriptionText': description.get_text('\n', strip=True),
        'descriptionHtml': description.decode_contents().strip(),
        'seniorityLevel': criteria.get('seniority level'),
        'employmentType': criteria.get('employment type'),
        'jobFunction': criteria.get('job function'),
        'industries': criteria.get('industries'),
    }
    return {key: value for key, value in detail.items() if value is not None}


class LinkedInJobsClient:
    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        proxy_url_factory: Callable[[], str | None] | None = None,
        max_retries: int = 3,
        min_delay: float = 0.4,
        rotate_every: int = 20,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.proxy_url = proxy_url
        self.proxy_url_factory = proxy_url_factory
        self.max_retries = max(1, max_retries)
        self.min_delay = max(0, min_delay)
        self.rotate_every = max(1, rotate_every)
        self.logger = logger or (lambda _: None)
        self._request_count = 0
        self._client = self._build_client(proxy_url)

    @staticmethod
    def _build_client(proxy_url: str | None) -> httpx.Client:
        return httpx.Client(
            headers=DEFAULT_HEADERS,
            proxy=proxy_url,
            follow_redirects=True,
            timeout=httpx.Timeout(30),
        )

    def _rotate(self) -> None:
        if not self.proxy_url_factory:
            return
        new_url = self.proxy_url_factory()
        if not new_url:
            return
        self._client.close()
        self.proxy_url = new_url
        self._client = self._build_client(new_url)
        self._request_count = 0
        self.logger('Rotated proxy session')

    def _get(self, url: str, *, params: dict | None = None) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            if self.proxy_url_factory and self._request_count >= self.rotate_every:
                self._rotate()
            if self._request_count:
                time.sleep(self.min_delay + random.uniform(0, self.min_delay / 2))
            try:
                response = self._client.get(url, params=params)
                self._request_count += 1
                if response.status_code in {403, 429, 999}:
                    raise LinkedInBlocked(f'LinkedIn returned HTTP {response.status_code}')
                response.raise_for_status()
                return response.text
            except (httpx.HTTPError, LinkedInBlocked) as exc:
                last_error = exc
                self.logger(f'Request attempt {attempt}/{self.max_retries} failed: {exc}')
                if attempt < self.max_retries:
                    self._rotate()
                    time.sleep(min(8, 2 ** (attempt - 1)))
        if isinstance(last_error, LinkedInBlocked):
            raise last_error
        raise LinkedInError(f'Request failed after {self.max_retries} attempts: {last_error}')

    def search_page(self, options: SearchOptions, *, start: int = 0) -> list[dict]:
        return parse_search_html(self._get(SEARCH_URL, params=options.params(start)))

    def get_job(self, job_id: str) -> dict:
        return parse_detail_html(self._get(DETAIL_URL.format(job_id=job_id)), job_id)

    def close(self) -> None:
        self._client.close()
