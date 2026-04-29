"""Reddit polling + metric repoll. Single writer, file-locked."""

from __future__ import annotations

import fcntl
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

import structlog
from sqlalchemy import select, update

from .config import AppConfig, load_config
from .db import session_scope
from .models import Post, PostMetric, SubredditRegistry, SystemState
from .reddit_client import (
    canonicalize_url, get_posts_by_id, get_subreddit_new, get_subreddit_rising,
)

log = structlog.get_logger()

LOCK_PATH = Path("/tmp/karen_finder_poll.lock")
NEW_LIMIT = 50
RISING_LIMIT = 25
REPOLL_BATCH_SIZE = 100


@contextmanager
def file_lock():
    """Yield True if the lock was acquired, False if another poll is already running."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "w")
    acquired = False
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            log.warning("poll_lock_busy", reason="another poll is in progress, skipping")
        yield acquired
    finally:
        if acquired:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()


def sync_subreddit_registry(cfg: AppConfig) -> None:
    """Ensure the `subreddits` table reflects config.yaml. Preserve `enabled=false` overrides."""
    with session_scope() as s:
        existing = {row.name: row for row in s.scalars(select(SubredditRegistry)).all()}
        for sub in cfg.subreddits:
            row = existing.get(sub.name)
            if row is None:
                s.add(SubredditRegistry(name=sub.name, enabled=True, weight=sub.weight))
            else:
                row.weight = sub.weight  # config wins on weight; enabled is sticky


def _domain_allowed(submission, allowed_domains: list[str]) -> bool:
    if submission.is_video:
        return True
    domain = (submission.domain or "").lower()
    return any(domain == d or domain.endswith("." + d) for d in allowed_domains)


def _extract_duration(submission) -> int | None:
    media = getattr(submission, "media", None) or {}
    rv = (media or {}).get("reddit_video") or {}
    duration = rv.get("duration")
    if isinstance(duration, int):
        return duration
    return None


def upsert_post_and_metric(s, submission, *, in_rising: bool, cfg: AppConfig, now: int) -> str | None:
    """Insert/update post + append a metric row. Returns post_id or None if filtered out."""
    if cfg.runtime.block_nsfw and submission.over_18:
        return None
    if not _domain_allowed(submission, cfg.allowed_domains):
        return None

    post_id = submission.fullname  # e.g. t3_abc
    canon_url = canonicalize_url(submission.url or "")

    crosspost_parent = None
    parent_list = getattr(submission, "crosspost_parent_list", None) or []
    if parent_list:
        crosspost_parent = parent_list[0].get("name")

    existing = s.get(Post, post_id)
    if existing is None:
        # Crosspost dedup: if we already have a row with this canonical URL, append to also_seen_in.
        existing_by_url = s.execute(
            select(Post).where(Post.url == canon_url).limit(1)
        ).scalar_one_or_none()
        if existing_by_url is not None and existing_by_url.subreddit != submission.subreddit.display_name:
            also = json.loads(existing_by_url.also_seen_in or "[]")
            sub_name = submission.subreddit.display_name
            if sub_name not in also and sub_name != existing_by_url.subreddit:
                also.append(sub_name)
                existing_by_url.also_seen_in = json.dumps(also)
                existing_by_url.last_updated_at = now
            return None  # do not create a new post row for the crosspost

        post = Post(
            post_id=post_id,
            subreddit=submission.subreddit.display_name,
            title=submission.title,
            author=str(submission.author) if submission.author else None,
            url=canon_url,
            domain=submission.domain,
            created_utc=int(submission.created_utc),
            is_video=bool(submission.is_video),
            is_self=bool(submission.is_self),
            nsfw=bool(submission.over_18),
            duration_s=_extract_duration(submission),
            crosspost_parent=crosspost_parent,
            also_seen_in=None,
            status="new",
            first_seen_at=now,
            last_updated_at=now,
        )
        s.add(post)
    else:
        existing.last_updated_at = now
        if existing.duration_s is None:
            existing.duration_s = _extract_duration(submission)

    metric = PostMetric(
        post_id=post_id,
        observed_at=now,
        score=int(submission.score or 0),
        num_comments=int(submission.num_comments or 0),
        upvote_ratio=float(submission.upvote_ratio or 0.0),
        in_rising=in_rising,
    )
    s.add(metric)
    return post_id


def repoll_active_posts(cfg: AppConfig, now: int) -> int:
    """Fetch fresh metrics for every recently-created post still in 'new' or 'shown' state."""
    cutoff = now - cfg.runtime.metric_repoll_max_age_h * 3600
    with session_scope() as s:
        ids = [
            pid for (pid,) in s.execute(
                select(Post.post_id).where(
                    Post.created_utc > cutoff,
                    Post.status.in_(("new", "shown")),
                )
            ).all()
        ]
    if not ids:
        return 0

    repolled = 0
    for i in range(0, len(ids), REPOLL_BATCH_SIZE):
        batch = ids[i : i + REPOLL_BATCH_SIZE]
        try:
            submissions = get_posts_by_id(batch)
        except Exception as e:  # pragma: no cover — network path
            log.warning("repoll_batch_failed", error=str(e), batch_size=len(batch))
            continue
        with session_scope() as s:
            for sub in submissions:
                obs_now = int(time.time())
                exists = s.execute(
                    select(PostMetric.id).where(
                        PostMetric.post_id == sub.fullname,
                        PostMetric.observed_at == obs_now,
                    )
                ).first()
                if exists:
                    continue
                s.add(PostMetric(
                    post_id=sub.fullname,
                    observed_at=obs_now,
                    score=sub.score,
                    num_comments=sub.num_comments,
                    upvote_ratio=sub.upvote_ratio,
                    in_rising=False,
                ))
                repolled += 1
    return repolled


def _set_state(key: str, value: str) -> None:
    with session_scope() as s:
        existing = s.get(SystemState, key)
        ts = int(time.time())
        if existing is None:
            s.add(SystemState(key=key, value=value, updated_at=ts))
        else:
            existing.value = value
            existing.updated_at = ts


def poll_once() -> dict:
    """Run a single ingestion pass over enabled subreddits + repoll active posts."""
    cfg = load_config()
    sync_subreddit_registry(cfg)
    now = int(time.time())

    with session_scope() as s:
        enabled = [
            row.name for row in s.scalars(
                select(SubredditRegistry).where(SubredditRegistry.enabled == True)  # noqa: E712
            ).all()
        ]

    new_posts = 0
    rising_posts = 0
    failed_subs: list[str] = []

    for sub_name in enabled:
        try:
            new_subs = get_subreddit_new(sub_name, NEW_LIMIT)
            rising_subs = get_subreddit_rising(sub_name, RISING_LIMIT)
            with session_scope() as s:
                for submission in new_subs:
                    pid = upsert_post_and_metric(s, submission, in_rising=False, cfg=cfg, now=int(time.time()))
                    if pid:
                        new_posts += 1
                for submission in rising_subs:
                    pid = upsert_post_and_metric(s, submission, in_rising=True, cfg=cfg, now=int(time.time()))
                    if pid:
                        rising_posts += 1
                s.execute(
                    update(SubredditRegistry)
                    .where(SubredditRegistry.name == sub_name)
                    .values(last_polled_at=int(time.time()))
                )
        except Exception as e:
            failed_subs.append(sub_name)
            log.warning("subreddit_poll_failed", subreddit=sub_name, error=str(e))

    repolled = repoll_active_posts(cfg, now)
    _set_state("last_poll_ok_at", str(int(time.time())))

    summary = {
        "new_posts_seen": new_posts,
        "rising_posts_seen": rising_posts,
        "metrics_repolled": repolled,
        "subreddits_polled": len(enabled) - len(failed_subs),
        "subreddits_failed": failed_subs,
    }
    log.info("poll_complete", **summary)
    return summary


def poll_with_lock() -> dict | None:
    with file_lock() as acquired:
        if not acquired:
            return None
        try:
            return poll_once()
        except Exception:
            log.exception("poll_once_failed")
            raise
