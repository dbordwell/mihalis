"""Nightly baseline computation: rolling mean/stdev of velocity metrics per
(subreddit, age_bucket). Two prior metric rows form a velocity sample; pairs
are bucketed by the post's age at the *latter* observation.
"""

from __future__ import annotations

import statistics
import time

import structlog
from sqlalchemy import select

from .db import session_scope
from .models import Post, PostMetric, SubredditBaseline
from .velocity import AGE_BUCKETS, MetricObservation, age_bucket, age_hours, hourly_velocity

log = structlog.get_logger()

LOOKBACK_DAYS = 14
MIN_SAMPLE_COUNT = 30


def _compute_velocity_samples(rows: list[PostMetric], created_utc: int) -> list[tuple[int, float, float]]:
    """Yield (age_bucket, score_velocity, comment_velocity) tuples per consecutive pair."""
    samples: list[tuple[int, float, float]] = []
    rows = sorted(rows, key=lambda r: r.observed_at)
    for prior, latest in zip(rows, rows[1:]):
        bucket = age_bucket(age_hours(latest.observed_at, created_utc))
        prior_obs = MetricObservation(prior.observed_at, prior.score, prior.num_comments)
        latest_obs = MetricObservation(latest.observed_at, latest.score, latest.num_comments)
        sv = hourly_velocity(latest_obs, prior_obs, "score")
        cv = hourly_velocity(latest_obs, prior_obs, "num_comments")
        samples.append((bucket, sv, cv))
    return samples


def compute_baselines() -> dict:
    """Recompute subreddit_baselines over the last LOOKBACK_DAYS. Idempotent upsert."""
    cutoff = int(time.time()) - LOOKBACK_DAYS * 86400
    by_key: dict[tuple[str, int, str], list[float]] = {}

    with session_scope() as s:
        posts = s.execute(
            select(Post).where(Post.first_seen_at >= cutoff)
        ).scalars().all()
        for post in posts:
            metrics = s.execute(
                select(PostMetric)
                .where(PostMetric.post_id == post.post_id, PostMetric.observed_at >= cutoff)
                .order_by(PostMetric.observed_at)
            ).scalars().all()
            if len(metrics) < 2:
                continue
            for bucket, sv, cv in _compute_velocity_samples(metrics, post.created_utc):
                if bucket not in AGE_BUCKETS:
                    continue
                by_key.setdefault((post.subreddit, bucket, "score_velocity"), []).append(sv)
                by_key.setdefault((post.subreddit, bucket, "comment_velocity"), []).append(cv)

        now = int(time.time())
        upserts = 0
        for (subreddit, bucket, metric_name), values in by_key.items():
            if len(values) < 2:
                continue
            mean = statistics.fmean(values)
            stdev = statistics.pstdev(values) if len(values) >= 2 else 0.0
            existing = s.get(SubredditBaseline, (subreddit, bucket, metric_name))
            if existing is None:
                s.add(SubredditBaseline(
                    subreddit=subreddit, age_bucket=bucket, metric=metric_name,
                    mean=mean, stdev=stdev, sample_count=len(values), computed_at=now,
                ))
            else:
                existing.mean = mean
                existing.stdev = stdev
                existing.sample_count = len(values)
                existing.computed_at = now
            upserts += 1

    summary = {"upserts": upserts, "subreddit_bucket_metrics": len(by_key)}
    log.info("baselines_computed", **summary)
    return summary


def is_baseline_trustworthy(sample_count: int) -> bool:
    return sample_count >= MIN_SAMPLE_COUNT
