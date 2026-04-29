"""YouTube Data API title-similarity saturation check (PLAN.md §12).

v1.0 only: search by post title; threshold against view count + channel subs.
Runs in shadow mode by default — logs decisions but does not affect rankings
until `saturation.mode` is flipped to `active` in config.yaml.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import structlog
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from rapidfuzz import fuzz

from .config import SaturationCfg, load_config, secrets
from .db import session_scope
from .models import Post, SaturationCheck, SystemState

log = structlog.get_logger()

SEARCH_COST = 100  # YouTube Data API quota units per search.list
DAILY_QUOTA = 10_000


@lru_cache(maxsize=1)
def _yt_client():
    return build("youtube", "v3", developerKey=secrets().youtube_api_key, cache_discovery=False)


_DECORATIONS = re.compile(r"\[(?:OC|Karen|Karens|Mod\s+Approved|Vid)\]|\((?:OC|video|vid)\)", re.I)
_EMOJI = re.compile(r"[^\w\s\-:.,!?']", re.UNICODE)


def clean_title(title: str) -> str:
    title = _DECORATIONS.sub(" ", title)
    title = _EMOJI.sub(" ", title)
    return " ".join(title.split())


@dataclass
class YTMatch:
    video_id: str
    title: str
    channel_id: str
    similarity: float
    views: int = 0
    channel_subs: int = 0


def _quota_remaining_today() -> int:
    """Read counter from system_state; reset at UTC midnight."""
    today = datetime.now(timezone.utc).date().isoformat()
    with session_scope() as s:
        used_row = s.get(SystemState, "youtube_quota_used_today")
        date_row = s.get(SystemState, "youtube_quota_date")
        used = int(used_row.value) if used_row else 0
        if date_row is None or date_row.value != today:
            used = 0
        return max(DAILY_QUOTA - used, 0)


def _bump_quota(units: int) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    now = int(time.time())
    with session_scope() as s:
        used_row = s.get(SystemState, "youtube_quota_used_today")
        date_row = s.get(SystemState, "youtube_quota_date")
        if date_row is None or date_row.value != today:
            if date_row is None:
                s.add(SystemState(key="youtube_quota_date", value=today, updated_at=now))
            else:
                date_row.value = today
                date_row.updated_at = now
            used = 0
        else:
            used = int(used_row.value) if used_row else 0
        used += units
        if used_row is None:
            s.add(SystemState(key="youtube_quota_used_today", value=str(used), updated_at=now))
        else:
            used_row.value = str(used)
            used_row.updated_at = now


def search_youtube(title: str, cfg: SaturationCfg) -> list[YTMatch]:
    """One search.list call. Returns up to 5 matches ranked by view count."""
    if _quota_remaining_today() < SEARCH_COST:
        log.warning("saturation_quota_exhausted")
        return []
    cleaned = clean_title(title)
    if not cleaned:
        return []

    published_after = (datetime.now(timezone.utc) - timedelta(days=cfg.lookback_days)).isoformat()

    try:
        resp = _yt_client().search().list(
            q=cleaned, part="snippet", type="video", order="viewCount",
            publishedAfter=published_after, maxResults=5,
        ).execute()
        _bump_quota(SEARCH_COST)
    except HttpError as e:
        log.warning("saturation_search_failed", error=str(e))
        return []

    matches: list[YTMatch] = []
    for item in resp.get("items", []):
        snip = item.get("snippet") or {}
        yt_title = snip.get("title", "")
        sim = fuzz.token_set_ratio(cleaned, yt_title) / 100.0
        if sim < cfg.title_similarity_threshold:
            continue
        matches.append(YTMatch(
            video_id=item["id"]["videoId"],
            title=yt_title,
            channel_id=snip.get("channelId", ""),
            similarity=sim,
        ))
    return matches


def hydrate_match_stats(matches: list[YTMatch]) -> None:
    """Batch fill view counts (videos.list) and channel subs (channels.list).

    Both endpoints cost 1 quota unit regardless of ID count (up to 50). Mutates in place.
    """
    if not matches:
        return
    yt = _yt_client()
    video_ids = list({m.video_id for m in matches if m.video_id})
    channel_ids = list({m.channel_id for m in matches if m.channel_id})

    try:
        if video_ids:
            v_resp = yt.videos().list(part="statistics", id=",".join(video_ids[:50])).execute()
            _bump_quota(1)
            view_by_id = {it["id"]: int((it.get("statistics") or {}).get("viewCount", 0)) for it in v_resp.get("items", [])}
            for m in matches:
                m.views = view_by_id.get(m.video_id, 0)

        if channel_ids:
            c_resp = yt.channels().list(part="statistics", id=",".join(channel_ids[:50])).execute()
            _bump_quota(1)
            subs_by_id = {it["id"]: int((it.get("statistics") or {}).get("subscriberCount", 0)) for it in c_resp.get("items", [])}
            for m in matches:
                m.channel_subs = subs_by_id.get(m.channel_id, 0)
    except HttpError as e:
        log.warning("saturation_hydrate_failed", error=str(e))


def evaluate(post: Post, cfg: SaturationCfg) -> SaturationCheck:
    """One saturation evaluation for one post. Returns an unsaved SaturationCheck."""
    matches = search_youtube(post.title, cfg)
    hydrate_match_stats(matches)
    matches.sort(key=lambda m: m.views, reverse=True)

    would_drop = any(
        m.views > cfg.drop_if_views_above and m.channel_subs > cfg.drop_if_channel_subs_above
        for m in matches
    )
    downrank = sum(m.views for m in matches) * cfg.downrank_per_1k_views / 1000.0

    top = matches[0] if matches else None
    return SaturationCheck(
        post_id=post.post_id,
        checked_at=int(time.time()),
        matches_found=len(matches),
        top_match_views=top.views if top else None,
        top_match_channel_subs=top.channel_subs if top else None,
        top_match_url=f"https://www.youtube.com/watch?v={top.video_id}" if top else None,
        top_match_title_similarity=top.similarity if top else None,
        would_drop=would_drop,
        would_downrank_score=downrank,
        in_effect=(cfg.mode == "active"),
    )


def check_candidates(post_ids: list[str]) -> list[SaturationCheck]:
    """Evaluate a batch of candidates. Persists checks; respects shadow vs. active mode."""
    cfg = load_config().saturation
    out: list[SaturationCheck] = []
    with session_scope() as s:
        posts = list(s.execute(
            __import__("sqlalchemy").select(Post).where(Post.post_id.in_(post_ids))
        ).scalars())
        for post in posts:
            check = evaluate(post, cfg)
            s.add(check)
            out.append(check)
    return out
