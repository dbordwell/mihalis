"""Reddit access via unauthenticated JSON endpoints (no API key required).

Reddit blocked our app-creation flow, so we dropped PRAW. The public JSON
endpoints (/r/X/new.json, /api/info.json) return the same data fields PRAW
exposes; we just lose PRAW's convenience layer. Rate limit is ~60 req/min
by IP; our load is ~2.5 req/min — comfortable margin.

The Submission class duck-types PRAW's submission interface so the rest of
the code (ingestion.py, bot.py) didn't need to change.
"""

from __future__ import annotations

from functools import lru_cache
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import secrets

log = structlog.get_logger()

_BASE = "https://www.reddit.com"
_TIMEOUT_S = 30


class RedditTransient(Exception):
    """Raised on 429s or 5xx — caller can retry."""


class Submission:
    """Duck-typed adapter around a Reddit JSON submission dict.

    Exposes the attributes ingestion.py uses; everything else passes through
    via __getattr__ for forward-compat if we add fields later.
    """

    __slots__ = ("_d",)

    def __init__(self, data: dict[str, Any]):
        self._d = data

    # Identity
    @property
    def fullname(self) -> str:
        return self._d.get("name") or f"t3_{self._d.get('id', '')}"

    @property
    def id(self) -> str:
        return self._d.get("id", "")

    # PRAW exposes submission.subreddit.display_name
    @property
    def subreddit(self):
        return SimpleNamespace(display_name=self._d.get("subreddit", ""))

    # Content
    @property
    def title(self) -> str:
        return self._d.get("title") or ""

    @property
    def author(self) -> str | None:
        a = self._d.get("author")
        return a if a else None

    @property
    def url(self) -> str:
        return self._d.get("url") or ""

    @property
    def domain(self) -> str | None:
        return self._d.get("domain")

    @property
    def created_utc(self) -> float:
        return self._d.get("created_utc") or 0

    @property
    def is_video(self) -> bool:
        return bool(self._d.get("is_video"))

    @property
    def is_self(self) -> bool:
        return bool(self._d.get("is_self"))

    @property
    def over_18(self) -> bool:
        return bool(self._d.get("over_18"))

    # Metrics
    @property
    def score(self) -> int:
        return int(self._d.get("score") or 0)

    @property
    def num_comments(self) -> int:
        return int(self._d.get("num_comments") or 0)

    @property
    def upvote_ratio(self) -> float:
        return float(self._d.get("upvote_ratio") or 0)

    # Crosspost / media
    @property
    def crosspost_parent_list(self):
        return self._d.get("crosspost_parent_list") or []

    @property
    def media(self):
        return self._d.get("media")

    @property
    def removed_by_category(self):
        return self._d.get("removed_by_category")

    def _fetch(self) -> None:
        """PRAW compat — already fetched at construction time."""
        return


# ---------- HTTP plumbing ----------

@lru_cache(maxsize=1)
def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = secrets().reddit_user_agent
    s.headers["Accept"] = "application/json"
    return s


@retry(
    retry=retry_if_exception_type(RedditTransient),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=1, max=20),
    reraise=True,
)
def _get_json(path: str, params: dict[str, Any] | None = None) -> dict:
    url = f"{_BASE}{path}"
    p = {"raw_json": 1, **(params or {})}
    resp = _session().get(url, params=p, timeout=_TIMEOUT_S)
    if resp.status_code == 429:
        log.warning("reddit_rate_limited", path=path)
        raise RedditTransient(f"429 on {path}")
    if 500 <= resp.status_code < 600:
        raise RedditTransient(f"{resp.status_code} on {path}")
    resp.raise_for_status()
    return resp.json()


def _listing_to_submissions(payload: dict) -> list[Submission]:
    children = (payload.get("data") or {}).get("children") or []
    return [Submission(c["data"]) for c in children if c.get("kind") == "t3" and c.get("data")]


# ---------- Public surface ----------

def get_subreddit_new(subreddit: str, limit: int = 50) -> list[Submission]:
    return _listing_to_submissions(_get_json(f"/r/{subreddit}/new.json", {"limit": limit}))


def get_subreddit_rising(subreddit: str, limit: int = 25) -> list[Submission]:
    return _listing_to_submissions(_get_json(f"/r/{subreddit}/rising.json", {"limit": limit}))


def get_posts_by_id(fullnames: list[str]) -> list[Submission]:
    """Batch fetch up to 100 posts by fullname (e.g. 't3_abc123'). Used for metric repoll."""
    if not fullnames:
        return []
    ids_param = ",".join(fullnames[:100])
    return _listing_to_submissions(_get_json("/api/info.json", {"id": ids_param}))


def get_submission_by_short_id(short_id: str) -> Submission | None:
    """Single fetch by short ID (no 't3_' prefix). Used for manual_pickup ingestion."""
    items = get_posts_by_id([f"t3_{short_id}"])
    return items[0] if items else None


# ---------- URL canonicalization (unchanged from PRAW version) ----------

def canonicalize_url(url: str) -> str:
    if not url:
        return url
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    keep_params: list[tuple[str, str]] = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=False):
        if "youtube" in host and k == "v":
            keep_params.append((k, v))
    query = urlencode(keep_params)
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), host, path, "", query, ""))
