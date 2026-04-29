from karen_finder.velocity import (
    AGE_BUCKETS,
    MetricObservation,
    acceleration,
    age_bucket,
    age_hours,
    hourly_velocity,
    z_score,
)


def obs(t: int, score: int, comments: int = 0) -> MetricObservation:
    return MetricObservation(observed_at=t, score=score, num_comments=comments)


def test_hourly_velocity_basic():
    # +60 score over 30 min → 120/hour
    assert hourly_velocity(obs(1800, 60), obs(0, 0), "score") == 120.0


def test_hourly_velocity_zero_window_safe():
    # Same timestamp must not divide-by-zero; treated as 1s window.
    assert hourly_velocity(obs(100, 1), obs(100, 0), "score") == 3600.0


def test_hourly_velocity_uses_attribute():
    # +5 comments over 600s → 30/hour
    assert hourly_velocity(obs(600, 0, 5), obs(0, 0, 0), "num_comments") == 30.0


def test_acceleration_increases_when_velocity_grows():
    # First half (0→1800, 30min): score 0→30  =>  60/hr
    # Second half (1800→3600, 30min): score 30→90  =>  120/hr (faster)
    a = acceleration(obs(3600, 90), obs(1800, 30), obs(0, 0), "score")
    assert a > 0


def test_acceleration_negative_when_velocity_decays():
    # First half (0→1800, 30min): score 0→60  =>  120/hr
    # Second half (1800→3600, 30min): score 60→90  =>  60/hr (slower)
    a = acceleration(obs(3600, 90), obs(1800, 60), obs(0, 0), "score")
    assert a < 0


def test_age_hours():
    assert age_hours(now_unix=3600, created_utc=0) == 1.0


def test_age_hours_clamps_negative_to_zero():
    assert age_hours(now_unix=0, created_utc=3600) == 0.0


def test_age_bucket_rounds_down_into_known_buckets():
    assert age_bucket(0.5) == 1
    assert age_bucket(1.0) == 1
    assert age_bucket(1.99) == 1
    assert age_bucket(2.0) == 2
    assert age_bucket(7.99) == 4
    assert age_bucket(8.0) == 8
    assert age_bucket(99) == 24


def test_age_bucket_only_returns_known_values():
    for h in (0, 0.1, 1, 2, 4, 8, 12, 24, 100):
        assert age_bucket(h) in AGE_BUCKETS


def test_z_score_basic():
    assert z_score(10.0, mean=5.0, stdev=2.5) == 2.0


def test_z_score_zero_stdev_does_not_divide_by_zero():
    # When stdev is zero we still want a finite, very-large value, not an exception.
    assert z_score(1.0, mean=0.0, stdev=0.0) > 1e3
