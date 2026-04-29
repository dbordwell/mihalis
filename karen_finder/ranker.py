"""Ranker: hard filters + composite score (PLAN.md §13).

Pulls every active candidate, filters, scores, and returns ordered tuples
of (composite, post_id, breakdown_dict). Saturation is applied as a negative
term only when the latest SaturationCheck has in_effect=True (active mode).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import structlog
from sqlalchemy import desc, select

from .config import AppConfig, load_config
from .db import session_scope
from .keywords import keyword_boost
from .models import Post, PostMetric, SaturationCheck, SubredditBaseline, SubredditRegistry
from .velocity import (
    AGE_BUCKETS, MetricObservation, age_bucket, age_hours, hourly_velocity, z_score,
)

log = structlog.get_logger()


@dataclass
class Candidate:
    post_id: str
    title: str
    subreddit: str
    composite: float
    breakdown: dict = field(default_factory=dict)
    age_h: float = 0.0
    latest_score: int = 0
    score_velocity_z: float = 0.0


def _latest_two_metrics(metrics: list[PostMetric]) -> tuple[PostMetric, PostMetric] | None:
    if len(metrics) < 2:
        return None
    sorted_m = sorted(metrics, key=lambda m: m.observed_at)
    return sorted_m[-2], sorted_m[-1]


def _baseline_lookup(s, subreddit: str, bucket: int, metric: str) -> SubredditBaseline | None:
    return s.get(SubredditBaseline, (subreddit, bucket, metric))


def _hard_filters(post: Post, latest: PostMetric, age_h: float, cfg: AppConfig, kw: float | None) -> str | None:
    """Return reason-string if the post is filtered, else None."""
    if kw is None:
        return "hard_blacklist"
    if post.status in ("claimed", "dismissed", "expired"):
        return f"status:{post.status}"
    if age_h < cfg.runtime.min_age_hours:
        return "too_young"
    if age_h > cfg.runtime.max_age_hours:
        return "too_old"
    if latest.score < cfg.runtime.min_score:
        return "low_score"
    if cfg.runtime.block_nsfw and post.nsfw:
        return "nsfw"
    if post.duration_s is not None:
        if post.duration_s < cfg.runtime.min_seconds or post.duration_s > cfg.runtime.max_seconds:
            return f"duration:{post.duration_s}s"
    if cfg.allowed_domains and post.domain and post.domain not in cfg.allowed_domains and not post.is_video:
        # leniency: native reddit videos pass even with a non-listed domain
        return f"domain:{post.domain}"
    return None


def score_candidates(*, now: int | None = None) -> list[Candidate]:
    """Filter + score every active post. Returns descending by composite."""
    cfg = load_config()
    weights = cfg.ranker_weights
    caps = cfg.ranker_caps
    now = now or int(time.time())

    candidates: list[Candidate] = []

    with session_scope() as s:
        sub_weights = {row.name: row.weight for row in s.scalars(select(SubredditRegistry)).all()}

        active_posts = s.execute(
            select(Post).where(Post.status.in_(("new", "shown")))
        ).scalars().all()

        for post in active_posts:
            metrics = s.execute(
                select(PostMetric)
                .where(PostMetric.post_id == post.post_id)
                .order_by(PostMetric.observed_at)
            ).scalars().all()
            if len(metrics) < 1:
                continue
            latest = metrics[-1]
            age_h = age_hours(now, post.created_utc)

            kw = keyword_boost(post.title, max_boost=caps.max_keyword_boost)
            reject = _hard_filters(post, latest, age_h, cfg, kw)
            if reject:
                continue

            # velocity / z-scores
            sv = cv = 0.0
            sv_z = cv_z = 0.0
            pair = _latest_two_metrics(metrics)
            if pair:
                prior, curr = pair
                prior_obs = MetricObservation(prior.observed_at, prior.score, prior.num_comments)
                curr_obs = MetricObservation(curr.observed_at, curr.score, curr.num_comments)
                sv = hourly_velocity(curr_obs, prior_obs, "score")
                cv = hourly_velocity(curr_obs, prior_obs, "num_comments")
                bucket = age_bucket(age_h)
                sv_base = _baseline_lookup(s, post.subreddit, bucket, "score_velocity")
                cv_base = _baseline_lookup(s, post.subreddit, bucket, "comment_velocity")
                if sv_base is not None and sv_base.sample_count >= 30:
                    sv_z = z_score(sv, sv_base.mean, sv_base.stdev)
                else:
                    sv_z = sv / 100.0  # cold-start: scaled raw value
                if cv_base is not None and cv_base.sample_count >= 30:
                    cv_z = z_score(cv, cv_base.mean, cv_base.stdev)
                else:
                    cv_z = cv / 10.0

            # saturation (only in effect during active mode)
            sat_term = 0.0
            sat_check = s.execute(
                select(SaturationCheck)
                .where(SaturationCheck.post_id == post.post_id)
                .order_by(desc(SaturationCheck.checked_at))
                .limit(1)
            ).scalar_one_or_none()
            if sat_check is not None and sat_check.in_effect:
                if sat_check.would_drop:
                    continue  # active drop
                sat_term = sat_check.would_downrank_score

            sub_weight = sub_weights.get(post.subreddit, 1.0)
            kw_val = kw or 0.0

            composite = (
                weights.score_velocity_z * sv_z
                + weights.comment_velocity_z * cv_z
                + weights.keyword_boost * kw_val
                + weights.subreddit_weight * sub_weight
                - weights.saturation_penalty * sat_term
            )

            candidates.append(Candidate(
                post_id=post.post_id,
                title=post.title,
                subreddit=post.subreddit,
                composite=composite,
                age_h=age_h,
                latest_score=latest.score,
                score_velocity_z=sv_z,
                breakdown={
                    "score_velocity": sv,
                    "comment_velocity": cv,
                    "score_velocity_z": sv_z,
                    "comment_velocity_z": cv_z,
                    "keyword_boost": kw_val,
                    "subreddit_weight": sub_weight,
                    "saturation_term": sat_term,
                    "duration_s": post.duration_s,
                },
            ))

    candidates.sort(key=lambda c: c.composite, reverse=True)
    return candidates


def top_n(n: int) -> list[Candidate]:
    return score_candidates()[:n]
