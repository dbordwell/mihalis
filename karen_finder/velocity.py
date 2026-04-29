"""Pure functions for velocity, acceleration, age bucketing, z-scores.

All functions take primitives or simple objects so they're trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass

AGE_BUCKETS = (1, 2, 4, 8, 12, 24)  # hours
SECONDS_PER_HOUR = 3600


@dataclass(frozen=True)
class MetricObservation:
    observed_at: int  # unix seconds
    score: int
    num_comments: int


def hourly_velocity(latest: MetricObservation, prior: MetricObservation, attr: str) -> float:
    dt_seconds = max(latest.observed_at - prior.observed_at, 1)
    delta = getattr(latest, attr) - getattr(prior, attr)
    return delta / dt_seconds * SECONDS_PER_HOUR


def acceleration(
    latest: MetricObservation, mid: MetricObservation, oldest: MetricObservation, attr: str = "score"
) -> float:
    """Change in velocity between (mid→latest) and (oldest→mid), per hour."""
    v_recent = hourly_velocity(latest, mid, attr)
    v_prior = hourly_velocity(mid, oldest, attr)
    dt_seconds = max(latest.observed_at - mid.observed_at, 1)
    return (v_recent - v_prior) / dt_seconds * SECONDS_PER_HOUR


def age_hours(now_unix: int, created_utc: int) -> float:
    return max(now_unix - created_utc, 0) / SECONDS_PER_HOUR


def age_bucket(age_h: float) -> int:
    """Round down to one of {1, 2, 4, 8, 12, 24}. Ages above 24 use the 24 bucket."""
    bucket = AGE_BUCKETS[0]
    for b in AGE_BUCKETS:
        if age_h >= b:
            bucket = b
        else:
            break
    return bucket


def z_score(raw: float, mean: float, stdev: float) -> float:
    return (raw - mean) / max(stdev, 1e-6)
