"""
Browserless YouTube transcript / subtitle client (httpx only).

Designed for use inside an Apify actor running on datacenter IPs, with an
optional proxy URL or a proxy-rotation hook.

Verified live on 2026-09-13 from a residential/ISP IP (see main() and the
findings block at the bottom of this docstring).

Approaches implemented (in the order the client tries them):

  (b) InnerTube `player` endpoint with a mobile-app client context
      POST https://www.youtube.com/youtubei/v1/player?prettyPrint=false
      context.client = ANDROID (fallback IOS).  Returns `captions.
      playerCaptionsTracklistRenderer.captionTracks[]` whose `baseUrl`
      (https://www.youtube.com/api/timedtext?...) can be fetched WITHOUT a
      PO token.  -> WORKS today without cookies.  This is the primary path.

  (a) Watch-page HTML -> ytInitialPlayerResponse.captions.captionTracks
      -> baseUrl + fmt=json3.  The HTML parses fine and is the only place that
      exposes publishDate/uploadDate (microformat) and ytInitialData, so it is
      still used for *metadata*.  However the WEB-client timedtext URLs now
      require a PO token (`pot=`) and return HTTP 200 with an EMPTY body from
      a plain IP.  The client detects the empty body and treats it as
      "PO-token gated" (PoTokenRequired) and moves on.

  (c) InnerTube `get_transcript` (the transcript side-panel endpoint)
      POST https://www.youtube.com/youtubei/v1/get_transcript with the
      base64 protobuf `params` (videoId + inner {kind, lang} + panel id).
      Even with the exact `params` blob copied from the watch page, the
      current WEB client version, VISITOR_DATA, page cookies and browser-like
      headers, it returns HTTP 400 "Precondition check failed" from a
      logged-out session.  Implemented as a last-resort fallback only.

Other endpoints used:
  - POST /youtubei/v1/browse          channel tabs (videos/shorts/streams),
                                      playlists (browseId="VL"+playlistId),
                                      continuation pages.
  - POST /youtubei/v1/navigation/resolve_url   @handle / custom URL -> UC id.

Translation: append `&tlang=<code>` to a timedtext URL.  Works, but Google
rate-limits translation requests aggressively per IP (HTTP 429 "Sorry...
automated queries" after a handful of calls) — rotate proxies for this.

Only dependency: httpx.
"""

from __future__ import annotations

import base64
import html as html_lib
import json
import random
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import httpx

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

YT = "https://www.youtube.com"
INNERTUBE_PLAYER = f"{YT}/youtubei/v1/player?prettyPrint=false"
INNERTUBE_BROWSE = f"{YT}/youtubei/v1/browse?prettyPrint=false"
INNERTUBE_RESOLVE = f"{YT}/youtubei/v1/navigation/resolve_url?prettyPrint=false"
INNERTUBE_GET_TRANSCRIPT = f"{YT}/youtubei/v1/get_transcript?prettyPrint=false"

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# InnerTube client contexts.  Versions verified working 2026-09-13.
# Update the version strings if YouTube starts returning UNPLAYABLE /
# "The page needs to be reloaded" for them.
INNERTUBE_CLIENTS: Dict[str, Dict[str, Any]] = {
    "ANDROID": {
        "context": {
            "clientName": "ANDROID",
            "clientVersion": "20.10.38",
            "androidSdkVersion": 30,
            "hl": "en",
            "gl": "US",
        },
        "headers": {
            "User-Agent": "com.google.android.youtube/20.10.38 (Linux; U; Android 11) gzip",
            "X-YouTube-Client-Name": "3",
            "X-YouTube-Client-Version": "20.10.38",
        },
    },
    "IOS": {
        "context": {
            "clientName": "IOS",
            "clientVersion": "20.10.4",
            "deviceMake": "Apple",
            "deviceModel": "iPhone16,2",
            "osName": "iPhone",
            "osVersion": "18.3.2.22D82",
            "hl": "en",
            "gl": "US",
        },
        "headers": {
            "User-Agent": "com.google.ios.youtube/20.10.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)",
            "X-YouTube-Client-Name": "5",
            "X-YouTube-Client-Version": "20.10.4",
        },
    },
    # WEB is only used for browse/resolve_url (listing).  For `player` it
    # returns UNPLAYABLE "Video unavailable" without a PO token (verified).
    "WEB": {
        "context": {
            "clientName": "WEB",
            "clientVersion": "2.20260911.01.00",
            "hl": "en",
            "gl": "US",
        },
        "headers": {
            "User-Agent": DESKTOP_UA,
            "X-YouTube-Client-Name": "1",
            "X-YouTube-Client-Version": "2.20260911.01.00",
            "Origin": YT,
        },
    },
}

# Channel tab `params` for /youtubei/v1/browse with browseId=UC...
CHANNEL_TAB_PARAMS = {
    "videos": "EgZ2aWRlb3PyBgQKAjoA",
    "shorts": "EgZzaG9ydHPyBgUKA5oBAA%3D%3D",
    "streams": "EgdzdHJlYW1z8gYECgJ6AA%3D%3D",
}

CONSENT_COOKIES = {"CONSENT": "YES+cb", "SOCS": "CAI"}

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID_RE = re.compile(r"^(PL|UU|LL|FL|OL|RD|UL)[A-Za-z0-9_-]+$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #

class YouTubeTranscriptError(Exception):
    """Base class for all errors raised by this module."""

    def __init__(self, message: str, video_id: Optional[str] = None):
        super().__init__(message)
        self.video_id = video_id


class InvalidVideoInput(YouTubeTranscriptError):
    """Could not extract an 11-char video id from the given input."""


class VideoUnavailable(YouTubeTranscriptError):
    """Video does not exist, was removed, or is otherwise unavailable."""


class VideoPrivate(VideoUnavailable):
    """Video is private."""


class AgeRestricted(YouTubeTranscriptError):
    """Video requires sign-in to confirm age (no cookies available)."""


class BotCheckRequired(YouTubeTranscriptError):
    """YouTube answered 'Sign in to confirm you're not a bot' (IP flagged)."""


class ConsentRequired(YouTubeTranscriptError):
    """Redirected to consent.youtube.com and the CONSENT/SOCS cookies did not help."""


class RateLimited(YouTubeTranscriptError):
    """HTTP 429/403 or Google's 'automated queries' page (retries exhausted)."""


class NoCaptionsAvailable(YouTubeTranscriptError):
    """The video has no caption tracks at all."""


class LanguageNotAvailable(YouTubeTranscriptError):
    """None of the requested languages is available (and no translation allowed)."""


class PoTokenRequired(YouTubeTranscriptError):
    """timedtext returned an empty body -> URL needs a PO token (WEB client URLs)."""


class TranscriptFetchFailed(YouTubeTranscriptError):
    """All approaches failed for reasons other than the specific ones above."""


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #

@dataclass
class CaptionTrack:
    language_code: str
    name: str
    kind: str                      # "manual" | "auto"
    is_translatable: bool
    base_url: str
    vss_id: Optional[str] = None
    source: str = "innertube"      # which approach produced this track

    @property
    def is_auto(self) -> bool:
        return self.kind == "auto"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("base_url", None)   # signed, short-lived -> do not export by default
        return d


@dataclass
class TranscriptSegment:
    start: float        # seconds
    duration: float     # seconds
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return {"start": round(self.start, 3), "duration": round(self.duration, 3), "text": self.text}


@dataclass
class VideoMetadata:
    video_id: str
    title: Optional[str] = None
    channel: Optional[str] = None
    channel_id: Optional[str] = None
    duration_seconds: Optional[int] = None
    view_count: Optional[int] = None
    publish_date: Optional[str] = None     # from watch-page microformat (YYYY-MM-DD or ISO)
    upload_date: Optional[str] = None
    description: Optional[str] = None
    keywords: List[str] = field(default_factory=list)
    is_live: bool = False
    is_short: Optional[bool] = None
    thumbnail_url: Optional[str] = None
    category: Optional[str] = None
    like_count: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TranscriptResult:
    video_id: str
    url: str
    metadata: VideoMetadata
    available_tracks: List[CaptionTrack]
    selected_track: Optional[CaptionTrack]
    translated_to: Optional[str]
    segments: List[TranscriptSegment]
    text: str
    approach: str                   # which approach produced the transcript
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_id": self.video_id,
            "url": self.url,
            "metadata": self.metadata.to_dict(),
            "available_tracks": [t.to_dict() for t in self.available_tracks],
            "selected_track": self.selected_track.to_dict() if self.selected_track else None,
            "translated_to": self.translated_to,
            "language": (self.translated_to or (self.selected_track.language_code if self.selected_track else None)),
            "segments": [s.to_dict() for s in self.segments],
            "text": self.text,
            "approach": self.approach,
            "diagnostics": self.diagnostics,
        }


@dataclass
class VideoListItem:
    video_id: str
    title: Optional[str] = None
    duration_text: Optional[str] = None
    published_text: Optional[str] = None
    view_count_text: Optional[str] = None
    is_short: bool = False
    url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def extract_video_id(value: str) -> str:
    """Accepts a bare id or any watch / youtu.be / shorts / embed / live URL."""
    value = (value or "").strip()
    if not value:
        raise InvalidVideoInput("empty input")
    if VIDEO_ID_RE.match(value):
        return value
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    try:
        u = urllib.parse.urlparse(value)
    except ValueError as e:
        raise InvalidVideoInput(f"cannot parse URL: {value}") from e
    host = (u.netloc or "").lower().split(":")[0]
    path = u.path or ""
    qs = urllib.parse.parse_qs(u.query)

    if host.endswith("youtu.be"):
        cand = path.strip("/").split("/")[0]
        if VIDEO_ID_RE.match(cand):
            return cand
    if "youtube" in host or "youtube-nocookie" in host:
        if "v" in qs and VIDEO_ID_RE.match(qs["v"][0]):
            return qs["v"][0]
        m = re.match(r"^/(?:shorts|embed|live|v|e)/([A-Za-z0-9_-]{11})", path)
        if m:
            return m.group(1)
        m = re.match(r"^/(?:watch|attribution_link)/?", path)
        if m and "v" in qs:
            return qs["v"][0]
    # last resort: any 11-char token after v= or /
    m = re.search(r"(?:v=|/)([A-Za-z0-9_-]{11})(?:[?&/#]|$)", value)
    if m:
        return m.group(1)
    raise InvalidVideoInput(f"could not extract a video id from: {value}")


def _clean_text(text: str) -> str:
    """Unescape HTML entities, collapse whitespace/newlines. Keeps [Music] etc."""
    if not text:
        return ""
    t = html_lib.unescape(text)
    t = t.replace("​", "")
    t = re.sub(r"[\r\n]+", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def _set_query_param(url: str, **params: Optional[str]) -> str:
    """Replace/add query params (used to force fmt=json3 and add tlang)."""
    u = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    q = [(k, v) for k, v in q if k not in params]
    for k, v in params.items():
        if v is not None:
            q.append((k, v))
    return urllib.parse.urlunparse(u._replace(query=urllib.parse.urlencode(q)))


def _walk(obj: Any) -> Iterable[dict]:
    """Yield every dict in a nested JSON structure (depth-first, in document order)."""
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            yield o
            stack.extend(reversed(list(o.values())))
        elif isinstance(o, list):
            stack.extend(reversed(o))


def _to_int(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _runs_text(node: Any) -> Optional[str]:
    if not isinstance(node, dict):
        return None
    if "simpleText" in node:
        return node["simpleText"]
    if "runs" in node:
        return "".join(r.get("text", "") for r in node["runs"])
    if "content" in node and isinstance(node["content"], str):
        return node["content"]
    return None


# -- protobuf helpers for get_transcript params ------------------------------ #

def _pb_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _pb_string(field_no: int, value: bytes) -> bytes:
    return _pb_varint((field_no << 3) | 2) + _pb_varint(len(value)) + value


def _pb_int(field_no: int, value: int) -> bytes:
    return _pb_varint(field_no << 3) + _pb_varint(value)


def build_get_transcript_params(video_id: str, language: str = "en", auto: bool = False) -> str:
    """Reproduces the `getTranscriptEndpoint.params` blob the web UI sends.

    Verified byte-for-byte identical to the blob found in the watch page for
    dQw4w9WgXcQ:  field1=videoId, field2=urlencoded-base64(inner{1:kind,
    2:lang, 3:""}), field3=1, field5=panel id, field6..8=1.
    """
    inner = _pb_string(1, b"asr" if auto else b"") + _pb_string(2, language.encode()) + _pb_string(3, b"")
    inner_b64 = urllib.parse.quote(base64.b64encode(inner).decode(), safe="")
    outer = (
        _pb_string(1, video_id.encode())
        + _pb_string(2, inner_b64.encode())
        + _pb_int(3, 1)
        + _pb_string(5, b"engagement-panel-searchable-transcript-search-panel")
        + _pb_int(6, 1)
        + _pb_int(7, 1)
        + _pb_int(8, 1)
    )
    return base64.b64encode(outer).decode()


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #

class YouTubeTranscriptClient:
    """
    Parameters
    ----------
    proxy_url:          static proxy (http://user:pass@host:port).
    proxy_url_factory:  callable returning a (new) proxy URL; called at start and
                        every time we hit a rate limit / bot check so the actor can
                        rotate Apify proxy sessions. Takes precedence over proxy_url.
    cookies:            optional dict of cookies (e.g. exported browser cookies for
                        age-restricted videos). CONSENT/SOCS are always added.
    max_retries:        retries per HTTP request on 429/403/5xx/network errors.
    timeout:            httpx timeout in seconds.
    min_delay:          polite delay (seconds) inserted between YouTube requests.
    """

    def __init__(
        self,
        proxy_url: Optional[str] = None,
        proxy_url_factory: Optional[Callable[[], Optional[str]]] = None,
        cookies: Optional[Dict[str, str]] = None,
        max_retries: int = 3,
        timeout: float = 30.0,
        min_delay: float = 0.0,
        user_agent: str = DESKTOP_UA,
        accept_language: str = "en-US,en;q=0.9",
        logger: Optional[Callable[[str], None]] = None,
    ):
        self._proxy_url = proxy_url
        self._proxy_url_factory = proxy_url_factory
        self._cookies = dict(CONSENT_COOKIES)
        if cookies:
            self._cookies.update(cookies)
        self.max_retries = max_retries
        self.timeout = timeout
        self.min_delay = min_delay
        self.user_agent = user_agent
        self.accept_language = accept_language
        self._log = logger or (lambda msg: None)
        self._last_request_ts = 0.0
        self._http: Optional[httpx.Client] = None
        self._current_proxy: Optional[str] = None
        self.stats: Dict[str, int] = {"requests": 0, "rate_limited": 0, "proxy_rotations": 0}
        self._open_http(initial=True)

    # -- http plumbing ------------------------------------------------------- #

    def _open_http(self, initial: bool = False) -> None:
        if self._http is not None:
            try:
                self._http.close()
            except Exception:
                pass
        proxy = None
        if self._proxy_url_factory is not None:
            proxy = self._proxy_url_factory()
            if not initial:
                self.stats["proxy_rotations"] += 1
        elif self._proxy_url:
            proxy = self._proxy_url
        self._current_proxy = proxy
        kwargs: Dict[str, Any] = dict(
            headers={"User-Agent": self.user_agent, "Accept-Language": self.accept_language},
            cookies=self._cookies,
            timeout=self.timeout,
            follow_redirects=True,
        )
        if proxy:
            try:
                self._http = httpx.Client(proxy=proxy, **kwargs)        # httpx >= 0.26
            except TypeError:
                self._http = httpx.Client(proxies=proxy, **kwargs)      # older httpx
        else:
            self._http = httpx.Client(**kwargs)

    def rotate_proxy(self) -> None:
        """Force a new proxy session (only meaningful with proxy_url_factory)."""
        self._open_http(initial=False)

    def close(self) -> None:
        if self._http is not None:
            self._http.close()

    def __enter__(self) -> "YouTubeTranscriptClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @staticmethod
    def _looks_rate_limited(resp: httpx.Response) -> bool:
        if resp.status_code in (429, 403):
            return True
        ct = resp.headers.get("content-type", "")
        if resp.status_code == 200 and "text/html" in ct:
            head = resp.text[:2000]
            if "automated queries" in head or "unusual traffic" in head or "/sorry/" in str(resp.url):
                return True
        return False

    def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        """HTTP request with backoff on 429/403/5xx and proxy rotation hook."""
        last_exc: Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            if self.min_delay:
                wait = self.min_delay - (time.time() - self._last_request_ts)
                if wait > 0:
                    time.sleep(wait)
            try:
                self.stats["requests"] += 1
                self._last_request_ts = time.time()
                resp = self._http.request(method, url, **kw)     # type: ignore[union-attr]
            except (httpx.TransportError, httpx.ProxyError) as e:
                last_exc = e
                self._log(f"network error {e!r} (attempt {attempt + 1})")
                if self._proxy_url_factory:
                    self.rotate_proxy()
                time.sleep(min(2 ** attempt, 20) + random.random())
                continue

            if self._looks_rate_limited(resp):
                self.stats["rate_limited"] += 1
                last_exc = RateLimited(f"HTTP {resp.status_code} rate limit / bot page for {url[:120]}")
                self._log(f"rate limited ({resp.status_code}) attempt {attempt + 1}")
                if self._proxy_url_factory:
                    self.rotate_proxy()
                retry_after = _to_int(resp.headers.get("retry-after"))
                time.sleep(retry_after or min(2 ** attempt * 2, 30) + random.random())
                continue
            if resp.status_code >= 500:
                last_exc = TranscriptFetchFailed(f"HTTP {resp.status_code} for {url[:120]}")
                time.sleep(min(2 ** attempt, 20) + random.random())
                continue
            return resp
        if isinstance(last_exc, YouTubeTranscriptError):
            raise last_exc
        raise TranscriptFetchFailed(f"request failed after retries: {last_exc!r}")

    def _innertube(self, endpoint: str, client: str, body: Dict[str, Any],
                   extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        cfg = INNERTUBE_CLIENTS[client]
        payload = {"context": {"client": dict(cfg["context"])}, **body}
        headers = {"Content-Type": "application/json", **cfg["headers"], **(extra_headers or {})}
        resp = self._request("POST", endpoint, json=payload, headers=headers)
        try:
            return resp.json()
        except ValueError as e:
            raise TranscriptFetchFailed(f"non-JSON InnerTube response ({resp.status_code}) from {endpoint}") from e

    # -- approach (b): InnerTube player ------------------------------------ #

    def innertube_player(self, video_id: str, client: str = "ANDROID") -> Dict[str, Any]:
        """Raw /youtubei/v1/player response for the given client context."""
        body = {"videoId": video_id, "contentCheckOk": True, "racyCheckOk": True}
        return self._innertube(INNERTUBE_PLAYER, client, body)

    @staticmethod
    def _check_playability(player: Dict[str, Any], video_id: str) -> None:
        ps = player.get("playabilityStatus") or {}
        status = ps.get("status")
        reason = ps.get("reason") or ""
        if status in (None, "OK", "LIVE_STREAM_OFFLINE", "CONTENT_CHECK_REQUIRED"):
            return
        # only human-readable texts (never raw JSON keys) feed the classifier
        sub_texts: List[str] = []
        for o in _walk(ps.get("errorScreen") or {}):
            for k in ("reason", "subreason"):
                t = _runs_text(o.get(k))
                if t:
                    sub_texts.append(t)
        low = (reason + " " + " ".join(sub_texts)).lower()
        if "not a bot" in low or ("confirm you" in low and "bot" in low):
            raise BotCheckRequired(f"{video_id}: {reason}", video_id)
        if status == "LOGIN_REQUIRED":
            if "age" in low or "inappropriate" in low:
                raise AgeRestricted(f"{video_id}: {reason}", video_id)
            raise BotCheckRequired(f"{video_id}: {reason or 'LOGIN_REQUIRED'}", video_id)
        if status == "AGE_CHECK_REQUIRED" or "age" in low:
            raise AgeRestricted(f"{video_id}: {reason}", video_id)
        if "private" in low:
            raise VideoPrivate(f"{video_id}: {reason}", video_id)
        if status in ("ERROR", "UNPLAYABLE"):
            raise VideoUnavailable(f"{video_id}: {reason or status}", video_id)
        raise VideoUnavailable(f"{video_id}: {status} {reason}", video_id)

    @staticmethod
    def _parse_caption_tracks(player: Dict[str, Any], source: str) -> List[CaptionTrack]:
        ptr = (player.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {}
        tracks: List[CaptionTrack] = []
        for t in ptr.get("captionTracks") or []:
            name = _runs_text(t.get("name")) or t.get("trackName") or t.get("languageCode", "")
            tracks.append(CaptionTrack(
                language_code=t.get("languageCode", ""),
                name=name,
                kind="auto" if t.get("kind") == "asr" else "manual",
                is_translatable=bool(t.get("isTranslatable", False)),
                base_url=t.get("baseUrl", ""),
                vss_id=t.get("vssId"),
                source=source,
            ))
        return tracks

    @staticmethod
    def _metadata_from_player(player: Dict[str, Any], video_id: str) -> VideoMetadata:
        vd = player.get("videoDetails") or {}
        mf = (player.get("microformat") or {}).get("playerMicroformatRenderer") or {}
        thumbs = (vd.get("thumbnail") or {}).get("thumbnails") or []
        return VideoMetadata(
            video_id=video_id,
            title=vd.get("title"),
            channel=vd.get("author"),
            channel_id=vd.get("channelId"),
            duration_seconds=_to_int(vd.get("lengthSeconds")),
            view_count=_to_int(vd.get("viewCount")),
            publish_date=mf.get("publishDate"),
            upload_date=mf.get("uploadDate"),
            description=vd.get("shortDescription"),
            keywords=list(vd.get("keywords") or []),
            is_live=bool(vd.get("isLiveContent") or vd.get("isLive")),
            thumbnail_url=thumbs[-1]["url"] if thumbs else None,
            category=mf.get("category"),
        )

    # -- approach (a): watch page HTML --------------------------------------- #

    def fetch_watch_page(self, video_id: str) -> Dict[str, Any]:
        """Returns {'player': ytInitialPlayerResponse, 'data': ytInitialData,
        'html': str, 'client_version': str, 'visitor_data': str, 'transcript_params': str|None}."""
        url = f"{YT}/watch?v={video_id}&hl=en&bpctr=9999999999&has_verified=1"
        resp = self._request("GET", url)
        if "consent.youtube.com" in str(resp.url):
            raise ConsentRequired(f"{video_id}: redirected to consent page despite CONSENT/SOCS cookies", video_id)
        html = resp.text
        out: Dict[str, Any] = {"html": html, "player": None, "data": None}
        m = re.search(r"ytInitialPlayerResponse\s*=\s*(\{.+?\})\s*;\s*(?:var\s|</script>)", html, re.S)
        if m:
            try:
                out["player"] = json.loads(m.group(1))
            except ValueError:
                pass
        m = re.search(r"ytInitialData\s*=\s*(\{.+?\})\s*;\s*</script>", html, re.S)
        if m:
            try:
                out["data"] = json.loads(m.group(1))
            except ValueError:
                pass
        m = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html)
        out["client_version"] = m.group(1) if m else None
        m = re.search(r'"VISITOR_DATA":"([^"]+)"', html)
        out["visitor_data"] = m.group(1) if m else None
        m = re.search(r'"getTranscriptEndpoint":\{"params":"([^"]+)"', html)
        out["transcript_params"] = m.group(1) if m else None
        if out["player"] is None and "Sign in to confirm" in html:
            raise BotCheckRequired(f"{video_id}: watch page asks to sign in (bot check)", video_id)
        return out

    @staticmethod
    def _enrich_metadata_from_page(meta: VideoMetadata, page: Dict[str, Any]) -> None:
        player = page.get("player") or {}
        pm = YouTubeTranscriptClient._metadata_from_player(player, meta.video_id)
        for f in ("title", "channel", "channel_id", "duration_seconds", "view_count", "publish_date",
                  "upload_date", "description", "thumbnail_url", "category"):
            if getattr(meta, f) in (None, "", 0) and getattr(pm, f) not in (None, ""):
                setattr(meta, f, getattr(pm, f))
        if not meta.keywords and pm.keywords:
            meta.keywords = pm.keywords
        data = page.get("data") or {}
        # like count: exact number is embedded as "likeCount":"<n>" in the page
        m = re.search(r'"likeCount":"(\d+)"', page.get("html") or "")
        if m:
            meta.like_count = _to_int(m.group(1))
        # relative date text fallback from ytInitialData (best effort)
        if meta.publish_date is None:
            for o in _walk(data):
                if "videoPrimaryInfoRenderer" in o:
                    meta.publish_date = _runs_text(o["videoPrimaryInfoRenderer"].get("dateText"))
                    break

    # -- timedtext fetch + parsing ------------------------------------------ #

    def fetch_timedtext(self, base_url: str, tlang: Optional[str] = None,
                        fmt: str = "json3", headers: Optional[Dict[str, str]] = None) -> List[TranscriptSegment]:
        """Fetch a caption track URL and parse it (json3 first, XML fallback)."""
        url = _set_query_param(base_url, fmt=fmt, tlang=tlang)
        resp = self._request("GET", url, headers=headers)
        body = resp.text
        if resp.status_code == 200 and not body.strip():
            raise PoTokenRequired("timedtext returned an empty body (PO token / pot= required for this URL)")
        if resp.status_code != 200:
            raise TranscriptFetchFailed(f"timedtext HTTP {resp.status_code}")
        segs = self.parse_timedtext(body)
        if not segs and fmt != "srv3":
            # some tracks are empty in json3 but fine in srv3 (rare) - one retry
            resp = self._request("GET", _set_query_param(base_url, fmt="srv3", tlang=tlang), headers=headers)
            segs = self.parse_timedtext(resp.text)
        return segs

    @staticmethod
    def parse_timedtext(body: str) -> List[TranscriptSegment]:
        body = body.lstrip("﻿ \n\r\t")
        if body.startswith("{"):
            return YouTubeTranscriptClient.parse_json3(body)
        if body.startswith("<"):
            return YouTubeTranscriptClient.parse_xml(body)
        return []

    @staticmethod
    def parse_json3(body: str) -> List[TranscriptSegment]:
        data = json.loads(body)
        out: List[TranscriptSegment] = []
        for ev in data.get("events") or []:
            segs = ev.get("segs")
            if not segs:
                continue
            text = _clean_text("".join(s.get("utf8", "") for s in segs))
            if not text:
                continue
            start_ms = ev.get("tStartMs", 0) or 0
            dur_ms = ev.get("dDurationMs", 0) or 0
            out.append(TranscriptSegment(start=start_ms / 1000.0, duration=dur_ms / 1000.0, text=text))
        return out

    @staticmethod
    def parse_xml(body: str) -> List[TranscriptSegment]:
        """Parses srv3 (<timedtext format="3"><body><p t d>) and legacy
        (<transcript><text start dur>) XML."""
        out: List[TranscriptSegment] = []
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return out
        if root.tag == "timedtext" or root.find("body") is not None:
            for p in root.iter("p"):
                t = _to_int(p.get("t")) or 0
                d = _to_int(p.get("d")) or 0
                text = _clean_text("".join(p.itertext()))
                if text:
                    out.append(TranscriptSegment(start=t / 1000.0, duration=d / 1000.0, text=text))
        else:
            for el in root.iter("text"):
                try:
                    start = float(el.get("start", 0))
                    dur = float(el.get("dur", 0))
                except ValueError:
                    continue
                text = _clean_text("".join(el.itertext()))
                if text:
                    out.append(TranscriptSegment(start=start, duration=dur, text=text))
        return out

    # -- approach (c): InnerTube get_transcript ----------------------------- #

    def innertube_get_transcript(self, video_id: str, language: str = "en", auto: bool = False,
                                 params: Optional[str] = None,
                                 page: Optional[Dict[str, Any]] = None) -> List[TranscriptSegment]:
        """Calls /youtubei/v1/get_transcript.  NOTE: as of 2026-09 this returns
        HTTP 400 'Precondition check failed' for logged-out requests even with
        the exact params from the watch page; kept as a last-resort fallback."""
        params = params or build_get_transcript_params(video_id, language, auto)
        cfg = INNERTUBE_CLIENTS["WEB"]
        ctx = dict(cfg["context"])
        headers = {"Referer": f"{YT}/watch?v={video_id}"}
        if page:
            if page.get("client_version"):
                ctx["clientVersion"] = page["client_version"]
                headers["X-YouTube-Client-Version"] = page["client_version"]
            if page.get("visitor_data"):
                ctx["visitorData"] = page["visitor_data"]
                headers["X-Goog-Visitor-Id"] = page["visitor_data"]
        payload = {"context": {"client": ctx}, "params": params}
        resp = self._request("POST", INNERTUBE_GET_TRANSCRIPT, json=payload,
                             headers={"Content-Type": "application/json", **cfg["headers"], **headers})
        try:
            j = resp.json()
        except ValueError as e:
            raise TranscriptFetchFailed(f"get_transcript non-JSON ({resp.status_code})") from e
        if "error" in j:
            raise TranscriptFetchFailed(f"get_transcript {j['error'].get('code')}: {j['error'].get('message')}")
        out: List[TranscriptSegment] = []
        for o in _walk(j):
            if "transcriptSegmentRenderer" in o:
                s = o["transcriptSegmentRenderer"]
                start = (_to_int(s.get("startMs")) or 0) / 1000.0
                end = (_to_int(s.get("endMs")) or 0) / 1000.0
                text = _clean_text(_runs_text(s.get("snippet")) or "")
                if text:
                    out.append(TranscriptSegment(start=start, duration=max(end - start, 0.0), text=text))
        out.sort(key=lambda s: s.start)
        return out

    # -- track selection ---------------------------------------------------- #

    @staticmethod
    def select_track(tracks: Sequence[CaptionTrack], languages: Optional[Sequence[str]] = None,
                     prefer_manual: bool = True) -> Optional[CaptionTrack]:
        """Pick a track: for each requested language in order (exact code, then
        prefix match e.g. 'en' ~ 'en-US'), prefer manual > auto (or the reverse).
        languages=None/[] -> any language, manual first."""
        if not tracks:
            return None

        def rank(t: CaptionTrack) -> int:
            return (0 if t.kind == "manual" else 1) if prefer_manual else (0 if t.kind == "auto" else 1)

        if languages:
            for lang in languages:
                lang_l = lang.lower()
                exact = [t for t in tracks if t.language_code.lower() == lang_l]
                if exact:
                    return sorted(exact, key=rank)[0]
                prefix = [t for t in tracks if t.language_code.lower().split("-")[0] == lang_l.split("-")[0]]
                if prefix:
                    return sorted(prefix, key=rank)[0]
            return None
        return sorted(tracks, key=rank)[0]

    # -- main entry point --------------------------------------------------- #

    def get_transcript(
        self,
        video: str,
        languages: Optional[Sequence[str]] = ("en",),
        prefer_manual: bool = True,
        translate_to: Optional[str] = None,
        fallback_to_any_language: bool = True,
        include_page_metadata: bool = True,
        player_clients: Sequence[str] = ("ANDROID", "IOS"),
        try_watch_page_captions: bool = True,
        try_get_transcript_endpoint: bool = True,
    ) -> TranscriptResult:
        """
        Fetch metadata + caption track list + transcript for one video.

        languages:      preference list; each entry is matched exactly then by
                        prefix.  None/empty -> any language.
        prefer_manual:  manual (human) tracks before auto-generated ones.
        translate_to:   target language code -> appends `tlang=` (uses YouTube's
                        auto-translation; requires a translatable track).  If the
                        selected track already has that language, no tlang is sent.
        fallback_to_any_language:
                        if none of `languages` exists, take the best available
                        track (and, when translate_to is unset, return it as-is).
        include_page_metadata:
                        fetch the watch page (~1.3 MB) to fill publish_date /
                        upload_date / like_count.  Set False for speed.
        """
        video_id = extract_video_id(video)
        url = f"{YT}/watch?v={video_id}"
        diagnostics: Dict[str, Any] = {"attempts": []}
        tracks: List[CaptionTrack] = []
        meta: Optional[VideoMetadata] = None
        page: Optional[Dict[str, Any]] = None
        first_error: Optional[YouTubeTranscriptError] = None

        # (b) InnerTube player with mobile clients -------------------------- #
        for client in player_clients:
            try:
                player = self.innertube_player(video_id, client)
                self._check_playability(player, video_id)
                tracks = self._parse_caption_tracks(player, f"innertube:{client}")
                meta = self._metadata_from_player(player, video_id)
                diagnostics["attempts"].append({"approach": f"player:{client}", "ok": True, "tracks": len(tracks)})
                if tracks:
                    break
            except (VideoUnavailable, AgeRestricted, BotCheckRequired) as e:
                diagnostics["attempts"].append({"approach": f"player:{client}", "ok": False, "error": type(e).__name__, "msg": str(e)})
                first_error = first_error or e
                # a different client can sometimes bypass age/bot gating -> keep trying
            except YouTubeTranscriptError as e:
                diagnostics["attempts"].append({"approach": f"player:{client}", "ok": False, "error": type(e).__name__, "msg": str(e)})
                first_error = first_error or e

        # (a) watch page: metadata (always, if requested) + tracks fallback --- #
        if include_page_metadata or not tracks:
            try:
                page = self.fetch_watch_page(video_id)
                if page.get("player"):
                    if meta is None:
                        self._check_playability(page["player"], video_id)
                        meta = self._metadata_from_player(page["player"], video_id)
                    else:
                        self._enrich_metadata_from_page(meta, page)
                    if not tracks:
                        tracks = self._parse_caption_tracks(page["player"], "watch_page")
                    diagnostics["attempts"].append({"approach": "watch_page", "ok": True,
                                                    "tracks": len(self._parse_caption_tracks(page["player"], "watch_page"))})
                else:
                    diagnostics["attempts"].append({"approach": "watch_page", "ok": False, "error": "no ytInitialPlayerResponse"})
            except YouTubeTranscriptError as e:
                diagnostics["attempts"].append({"approach": "watch_page", "ok": False, "error": type(e).__name__, "msg": str(e)})
                first_error = first_error or e

        if meta is None:
            if first_error:
                raise first_error
            raise TranscriptFetchFailed(f"{video_id}: could not load video info", video_id)
        if meta.is_short is None:
            meta.is_short = bool(meta.duration_seconds and meta.duration_seconds <= 180 and "/shorts/" in video)

        if not tracks:
            raise NoCaptionsAvailable(f"{video_id}: no caption tracks ({meta.title!r})", video_id)

        # select track ------------------------------------------------------ #
        selected = self.select_track(tracks, languages, prefer_manual)
        if selected is None and translate_to:
            # need any translatable track as the source
            cands = [t for t in tracks if t.is_translatable] or list(tracks)
            selected = self.select_track(cands, None, prefer_manual)
        if selected is None and fallback_to_any_language:
            selected = self.select_track(tracks, None, prefer_manual)
        if selected is None:
            raise LanguageNotAvailable(
                f"{video_id}: none of {list(languages or [])} available; have "
                f"{[(t.language_code, t.kind) for t in tracks]}", video_id)

        tlang: Optional[str] = None
        if translate_to and selected.language_code.lower().split("-")[0] != translate_to.lower().split("-")[0]:
            tlang = translate_to

        # fetch transcript: try the selected track's URL, then siblings ------- #
        segments: List[TranscriptSegment] = []
        approach = ""
        errors: List[str] = []
        ordered = [selected] + [t for t in tracks if t is not selected and t.language_code == selected.language_code]
        for t in ordered:
            if not t.base_url:
                continue
            hdrs = None
            if t.source.startswith("innertube:"):
                hdrs = {"User-Agent": INNERTUBE_CLIENTS[t.source.split(":")[1]]["headers"]["User-Agent"]}
            try:
                segments = self.fetch_timedtext(t.base_url, tlang=tlang, headers=hdrs)
                if segments:
                    selected = t
                    approach = f"timedtext:{t.source}"
                    break
                errors.append(f"{t.source}: empty transcript")
            except PoTokenRequired as e:
                errors.append(f"{t.source}: {e}")
                diagnostics["po_token_gated"] = True
            except RateLimited as e:
                errors.append(f"{t.source}: {e}")
                diagnostics["rate_limited"] = True
                break
            except YouTubeTranscriptError as e:
                errors.append(f"{t.source}: {e}")

        # if the mobile-client tracks failed, try the watch-page tracks (a) --- #
        if not segments and try_watch_page_captions and page is None:
            try:
                page = self.fetch_watch_page(video_id)
            except YouTubeTranscriptError as e:
                errors.append(f"watch_page: {e}")
        if not segments and try_watch_page_captions and page and page.get("player"):
            for t in self._parse_caption_tracks(page["player"], "watch_page"):
                if t.language_code != selected.language_code or t.kind != selected.kind:
                    continue
                try:
                    segments = self.fetch_timedtext(t.base_url, tlang=tlang)
                    if segments:
                        approach = "timedtext:watch_page"
                        break
                except PoTokenRequired as e:
                    errors.append(f"watch_page: {e}")
                    diagnostics["po_token_gated"] = True
                except YouTubeTranscriptError as e:
                    errors.append(f"watch_page: {e}")

        # (c) get_transcript endpoint (currently 400 for logged-out) ---------- #
        if not segments and try_get_transcript_endpoint and not tlang:
            try:
                segments = self.innertube_get_transcript(
                    video_id, selected.language_code, auto=selected.is_auto,
                    params=(page or {}).get("transcript_params"), page=page)
                if segments:
                    approach = "innertube:get_transcript"
            except YouTubeTranscriptError as e:
                errors.append(f"get_transcript: {e}")

        diagnostics["errors"] = errors
        diagnostics["proxy"] = bool(self._current_proxy)
        if not segments:
            if diagnostics.get("rate_limited"):
                raise RateLimited(f"{video_id}: rate limited while fetching captions: {errors}", video_id)
            raise TranscriptFetchFailed(f"{video_id}: all caption fetch approaches failed: {errors}", video_id)

        text = " ".join(s.text for s in segments)
        return TranscriptResult(
            video_id=video_id, url=url, metadata=meta, available_tracks=tracks,
            selected_track=selected, translated_to=tlang, segments=segments, text=text,
            approach=approach, diagnostics=diagnostics,
        )

    def list_caption_tracks(self, video: str) -> List[CaptionTrack]:
        """Only the track list (one InnerTube call)."""
        video_id = extract_video_id(video)
        for client in ("ANDROID", "IOS"):
            player = self.innertube_player(video_id, client)
            self._check_playability(player, video_id)
            tracks = self._parse_caption_tracks(player, f"innertube:{client}")
            if tracks:
                return tracks
        return []

    # -- listing: channels & playlists -------------------------------------- #

    def resolve_channel_id(self, channel: str) -> str:
        """Accepts UC id, @handle, /c/ or /user/ or /channel/ URL -> UC id."""
        channel = channel.strip()
        if CHANNEL_ID_RE.match(channel):
            return channel
        m = re.search(r"/channel/(UC[A-Za-z0-9_-]{22})", channel)
        if m:
            return m.group(1)
        if channel.startswith("@"):
            url = f"{YT}/{channel}"
        elif re.match(r"^https?://", channel, re.I):
            url = channel
        elif "/" in channel:
            url = f"{YT}/{channel.lstrip('/')}"
        else:
            url = f"{YT}/@{channel}"
        # InnerTube resolve_url
        try:
            j = self._innertube(INNERTUBE_RESOLVE, "WEB", {"url": url})
            bid = ((j.get("endpoint") or {}).get("browseEndpoint") or {}).get("browseId")
            if bid and CHANNEL_ID_RE.match(bid):
                return bid
        except YouTubeTranscriptError:
            pass
        # HTML fallback
        resp = self._request("GET", url)
        m = re.search(r'"externalId":"(UC[A-Za-z0-9_-]{22})"', resp.text) or \
            re.search(r'"channelId":"(UC[A-Za-z0-9_-]{22})"', resp.text)
        if m:
            return m.group(1)
        raise InvalidVideoInput(f"could not resolve channel: {channel}")

    @staticmethod
    def extract_playlist_id(value: str) -> str:
        value = value.strip()
        if PLAYLIST_ID_RE.match(value):
            return value
        m = re.search(r"[?&]list=([A-Za-z0-9_-]+)", value)
        if m:
            return m.group(1)
        raise InvalidVideoInput(f"could not extract a playlist id from: {value}")

    @staticmethod
    def _parse_listing(j: Dict[str, Any]) -> tuple[List[VideoListItem], List[str]]:
        items: List[VideoListItem] = []
        conts: List[str] = []
        seen: set = set()
        for o in _walk(j):
            item: Optional[VideoListItem] = None
            if "lockupViewModel" in o:                       # 2025+ UI
                lv = o["lockupViewModel"]
                if lv.get("contentType") not in (None, "LOCKUP_CONTENT_TYPE_VIDEO", "LOCKUP_CONTENT_TYPE_SHORT"):
                    continue
                vid = lv.get("contentId")
                if not vid or not VIDEO_ID_RE.match(vid):
                    continue
                md = (lv.get("metadata") or {}).get("lockupMetadataViewModel") or {}
                rows = ((md.get("metadata") or {}).get("contentMetadataViewModel") or {}).get("metadataRows") or []
                parts = [p.get("text", {}).get("content") for r in rows for p in (r.get("metadataParts") or [])]
                parts = [p for p in parts if p]
                dur = None
                is_short = lv.get("contentType") == "LOCKUP_CONTENT_TYPE_SHORT"
                for b in _walk(lv.get("contentImage") or {}):
                    if "thumbnailBadgeViewModel" in b and b["thumbnailBadgeViewModel"].get("text"):
                        txt = b["thumbnailBadgeViewModel"]["text"]
                        if re.match(r"^\d+:\d\d(:\d\d)?$", txt):
                            dur = txt
                        elif txt.upper() == "SHORTS":
                            is_short = True
                views = next((p for p in parts if "view" in p.lower()), None)
                when = next((p for p in parts if "ago" in p.lower() or "streamed" in p.lower() or "premier" in p.lower()), None)
                item = VideoListItem(vid, _runs_text(md.get("title")), dur, when, views, is_short)
            elif "videoRenderer" in o:                        # legacy
                vr = o["videoRenderer"]
                vid = vr.get("videoId")
                if vid:
                    item = VideoListItem(vid, _runs_text(vr.get("title")), _runs_text(vr.get("lengthText")),
                                         _runs_text(vr.get("publishedTimeText")), _runs_text(vr.get("viewCountText")))
            elif "playlistVideoRenderer" in o:
                pv = o["playlistVideoRenderer"]
                vid = pv.get("videoId")
                if vid:
                    secs = _to_int(pv.get("lengthSeconds"))
                    item = VideoListItem(vid, _runs_text(pv.get("title")), _runs_text(pv.get("lengthText")),
                                         _runs_text(pv.get("videoInfo")), None, bool(secs and secs <= 60))
            elif "reelItemRenderer" in o:
                rr = o["reelItemRenderer"]
                vid = rr.get("videoId")
                if vid:
                    item = VideoListItem(vid, _runs_text(rr.get("headline")), None, None, _runs_text(rr.get("viewCountText")), True)
            elif "shortsLockupViewModel" in o:
                sv = o["shortsLockupViewModel"]
                vid = ((sv.get("onTap") or {}).get("innertubeCommand") or {}).get("reelWatchEndpoint", {}).get("videoId")
                if not vid:
                    ent = sv.get("entityId", "")
                    vid = ent.rsplit("-", 1)[-1] if VIDEO_ID_RE.match(ent.rsplit("-", 1)[-1]) else None
                if vid:
                    views = ((sv.get("overlayMetadata") or {}).get("secondaryText") or {}).get("content")
                    item = VideoListItem(vid, ((sv.get("overlayMetadata") or {}).get("primaryText") or {}).get("content"),
                                         None, None, views, True)
            elif "continuationCommand" in o and isinstance(o["continuationCommand"], dict):
                tok = o["continuationCommand"].get("token")
                if tok:
                    conts.append(tok)
            if item and item.video_id not in seen:
                seen.add(item.video_id)
                item.url = f"{YT}/shorts/{item.video_id}" if item.is_short else f"{YT}/watch?v={item.video_id}"
                items.append(item)
        uniq: List[str] = []
        for t in conts:
            if t not in uniq:
                uniq.append(t)
        return items, uniq

    def _browse_all(self, first_body: Dict[str, Any], max_videos: int, max_pages: int = 200) -> List[VideoListItem]:
        out: List[VideoListItem] = []
        seen: set = set()
        body = first_body
        pages = 0
        while body and pages < max_pages and len(out) < max_videos:
            j = self._innertube(INNERTUBE_BROWSE, "WEB", body)
            pages += 1
            alerts = [_runs_text((a.get("alertRenderer") or a.get("alertWithButtonRenderer") or {}).get("text"))
                      for a in j.get("alerts") or []]
            if alerts and not out:
                raise VideoUnavailable(f"browse alert: {alerts}")
            items, conts = self._parse_listing(j)
            new = 0
            for it in items:
                if it.video_id not in seen:
                    seen.add(it.video_id)
                    out.append(it)
                    new += 1
                    if len(out) >= max_videos:
                        break
            if not conts or new == 0:
                break
            body = {"continuation": conts[0]}
        return out[:max_videos]

    def list_playlist_videos(self, playlist: str, max_videos: int = 500) -> List[VideoListItem]:
        """Public playlist (PL..., UU... uploads, etc.) -> up to N videos, 100/page."""
        pid = self.extract_playlist_id(playlist)
        return self._browse_all({"browseId": "VL" + pid}, max_videos)

    def list_channel_videos(self, channel: str, max_videos: int = 500,
                            source: str = "uploads") -> List[VideoListItem]:
        """
        channel: UC id, @handle, or channel URL.
        source:  "uploads" -> the UU<channel> uploads playlist (videos + shorts +
                              streams, newest first, 100 per page)  [default]
                 "videos"  -> the channel's Videos tab (long-form only, 30/page)
                 "shorts"  -> the Shorts tab
                 "streams" -> the Live tab
        """
        cid = self.resolve_channel_id(channel)
        if source == "uploads":
            return self.list_playlist_videos("UU" + cid[2:], max_videos)
        if source not in CHANNEL_TAB_PARAMS:
            raise ValueError(f"unknown source {source!r}")
        items = self._browse_all({"browseId": cid, "params": CHANNEL_TAB_PARAMS[source]}, max_videos)
        if source == "shorts":
            for it in items:
                it.is_short = True
                it.url = f"{YT}/shorts/{it.video_id}"
        return items

    def channel_info(self, channel: str) -> Dict[str, Any]:
        cid = self.resolve_channel_id(channel)
        j = self._innertube(INNERTUBE_BROWSE, "WEB", {"browseId": cid})
        md = (j.get("metadata") or {}).get("channelMetadataRenderer") or {}
        return {
            "channel_id": cid,
            "title": md.get("title"),
            "description": md.get("description"),
            "url": md.get("channelUrl"),
            "vanity_url": md.get("vanityChannelUrl"),
            "keywords": md.get("keywords"),
            "avatar": ((md.get("avatar") or {}).get("thumbnails") or [{}])[-1].get("url"),
        }


# --------------------------------------------------------------------------- #
# Demo / live verification
# --------------------------------------------------------------------------- #

def main() -> None:
    import sys

    tests = [
        ("dQw4w9WgXcQ", "manual + auto captions (Rick Astley)"),
        ("https://www.youtube.com/shorts/ihRdK3x3cUY", "Shorts URL, auto captions only"),
        ("https://youtu.be/erqkhJ8uECM", "no captions at all"),
        ("https://www.youtube.com/embed/aaaaaaaaaaa", "nonexistent video"),
    ]
    if len(sys.argv) > 1:
        tests = [(a, "cli arg") for a in sys.argv[1:]]

    log = lambda m: print("   [log]", m)
    with YouTubeTranscriptClient(logger=log) as yt:
        for video, label in tests:
            print(f"\n=== {video}  ({label}) ===")
            try:
                r = yt.get_transcript(video, languages=["en", "ko"], include_page_metadata=True)
                m = r.metadata
                print(f"  title      : {m.title}")
                print(f"  channel    : {m.channel} ({m.channel_id})")
                print(f"  duration   : {m.duration_seconds}s  views: {m.view_count}  likes: {m.like_count}")
                print(f"  published  : {m.publish_date}  uploaded: {m.upload_date}")
                print(f"  tracks     : {[(t.language_code, t.kind, t.is_translatable) for t in r.available_tracks]}")
                print(f"  selected   : {r.selected_track.language_code} ({r.selected_track.kind}) via {r.approach}")
                print(f"  segments   : {len(r.segments)}  first: {r.segments[0].to_dict()}")
                print(f"  text[:120] : {r.text[:120]!r}")
                print(f"  attempts   : {r.diagnostics['attempts']}")
                if r.diagnostics.get("errors"):
                    print(f"  errors     : {r.diagnostics['errors']}")
            except YouTubeTranscriptError as e:
                print(f"  -> {type(e).__name__}: {e}")

        # per-approach report for the reference video
        vid = "dQw4w9WgXcQ"
        print(f"\n=== per-approach report for {vid} ===")
        for client in ("ANDROID", "IOS", "WEB"):
            try:
                p = yt.innertube_player(vid, client)
                ps = p.get("playabilityStatus", {})
                tr = yt._parse_caption_tracks(p, client)
                line = f"  player:{client:<8} status={ps.get('status')} reason={ps.get('reason')!r} tracks={len(tr)}"
                if tr:
                    try:
                        segs = yt.fetch_timedtext(tr[0].base_url, headers={"User-Agent": INNERTUBE_CLIENTS[client]['headers']['User-Agent']})
                        line += f" timedtext(json3)={len(segs)} segs, pot_in_url={'pot=' in tr[0].base_url}"
                    except YouTubeTranscriptError as e:
                        line += f" timedtext -> {type(e).__name__}: {e}"
                print(line)
            except YouTubeTranscriptError as e:
                print(f"  player:{client:<8} -> {type(e).__name__}: {e}")
        try:
            page = yt.fetch_watch_page(vid)
            tr = yt._parse_caption_tracks(page["player"], "watch_page") if page.get("player") else []
            line = f"  watch_page       tracks={len(tr)} client_version={page.get('client_version')}"
            if tr:
                try:
                    segs = yt.fetch_timedtext(tr[0].base_url)
                    line += f" timedtext={len(segs)} segs"
                except YouTubeTranscriptError as e:
                    line += f" timedtext -> {type(e).__name__}: {e}"
            print(line)
            try:
                segs = yt.innertube_get_transcript(vid, "en", params=page.get("transcript_params"), page=page)
                print(f"  get_transcript   segs={len(segs)}")
            except YouTubeTranscriptError as e:
                print(f"  get_transcript   -> {type(e).__name__}: {e}")
            print(f"  params match     built={build_get_transcript_params(vid, 'en') == page.get('transcript_params')}")
        except YouTubeTranscriptError as e:
            print(f"  watch_page -> {type(e).__name__}: {e}")

        # translation
        print(f"\n=== translation (tlang=fr) for {vid} ===")
        try:
            r = yt.get_transcript(vid, languages=["en"], translate_to="fr", include_page_metadata=False)
            print(f"  translated_to={r.translated_to} segs={len(r.segments)} first={r.segments[0].text!r}")
        except YouTubeTranscriptError as e:
            print(f"  -> {type(e).__name__}: {e}")

        # listing
        print("\n=== channel / playlist listing ===")
        try:
            info = yt.channel_info("@RickAstleyYT")
            print(f"  channel_info: {info['channel_id']} {info['title']!r}")
            vids = yt.list_channel_videos("@RickAstleyYT", max_videos=130, source="uploads")
            print(f"  uploads (max 130): {len(vids)} first={vids[0].to_dict()}")
            vids = yt.list_channel_videos("UCuAXFkgsw1L7xaCfnd5JJOw", max_videos=45, source="videos")
            print(f"  videos tab (max 45): {len(vids)} sample={[(v.video_id, v.duration_text) for v in vids[:3]]}")
            shorts = yt.list_channel_videos("https://www.youtube.com/@RickAstleyYT", max_videos=10, source="shorts")
            print(f"  shorts tab (max 10): {len(shorts)} sample={[(v.video_id, v.is_short) for v in shorts[:3]]}")
            pl = yt.list_playlist_videos("https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI", max_videos=120)
            print(f"  playlist (max 120): {len(pl)} sample={[v.video_id for v in pl[:3]]}")
        except YouTubeTranscriptError as e:
            print(f"  -> {type(e).__name__}: {e}")

        print(f"\nstats: {yt.stats}")


if __name__ == "__main__":
    main()
