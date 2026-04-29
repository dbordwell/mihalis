"""APScheduler wiring. AsyncIO so we can integrate with the Telegram bot later."""

from __future__ import annotations

import asyncio
import time

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from datetime import datetime
from typing import TYPE_CHECKING

from .baselines import compute_baselines
from .config import load_config, secrets
from .db import session_scope
from .ingestion import poll_with_lock
from .models import Post, SystemState

if TYPE_CHECKING:
    from telegram.ext import Application

log = structlog.get_logger()


async def _poll_job() -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, poll_with_lock)


async def _heartbeat_check_job() -> None:
    cfg = load_config()
    threshold = cfg.watchdog.heartbeat_max_age_minutes * 60
    with session_scope() as s:
        row = s.execute(
            select(SystemState).where(SystemState.key == "last_poll_ok_at")
        ).scalar_one_or_none()
    if row is None:
        return
    age = int(time.time()) - int(row.value)
    if age > threshold:
        log.warning("heartbeat_stale", age_seconds=age, threshold_seconds=threshold)
        # TODO Phase 3: page operator via Telegram


async def _smoke_check_job() -> None:
    log.info("smoke_check_running")
    # TODO Phase 3: query yesterday's candidate count and error count; alert if both zero.


async def _baseline_job() -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, compute_baselines)


async def _expire_old_posts_job() -> None:
    """Mark posts older than 7 days as 'expired' so they fall out of digest pipelines."""
    cutoff = int(time.time()) - 7 * 86400
    from sqlalchemy import update
    with session_scope() as s:
        s.execute(
            update(Post)
            .where(Post.created_utc < cutoff, Post.status.in_(("new", "shown")))
            .values(status="expired")
        )
    log.info("expire_old_posts_done", cutoff=cutoff)


def _within_quiet_hours(now: datetime, start_hm: str, end_hm: str) -> bool:
    sh, sm = map(int, start_hm.split(":"))
    eh, em = map(int, end_hm.split(":"))
    cur = now.hour * 60 + now.minute
    start = sh * 60 + sm
    end = eh * 60 + em
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end


def _make_digest_jobs(application: "Application", sched: AsyncIOScheduler) -> None:
    """Wire digest + breaking-alert jobs that need the Telegram Application."""
    from .telegram_bot import (
        send_breaking_alert, send_consumer_digest, send_operator_digest,
    )
    from .ranker import score_candidates

    cfg = load_config()
    tz = secrets().tz

    async def _morning():
        await send_consumer_digest(application, digest_type="daily_morning")

    async def _evening():
        await send_consumer_digest(application, digest_type="daily_evening")

    async def _operator():
        await send_operator_digest(application)

    async def _breaking_scan():
        if not cfg.breaking_alerts.enabled:
            return
        # daily cap
        from sqlalchemy import select
        since_24h = int(time.time()) - 86400
        from .models import DigestHistory
        with session_scope() as s:
            sent_today = s.execute(
                select(DigestHistory).where(
                    DigestHistory.digest_type == "breaking",
                    DigestHistory.sent_at >= since_24h,
                )
            ).scalars().all()
        if len(sent_today) >= cfg.breaking_alerts.daily_cap:
            return

        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo
        now_local = _dt.now(ZoneInfo(tz))
        if _within_quiet_hours(
            now_local,
            cfg.breaking_alerts.quiet_hours_start_local,
            cfg.breaking_alerts.quiet_hours_end_local,
        ):
            return  # surface in morning digest instead

        threshold = cfg.breaking_alerts.z_score_threshold
        max_age = cfg.breaking_alerts.max_age_hours
        for c in score_candidates():
            if c.age_h > max_age:
                continue
            if c.score_velocity_z < threshold:
                continue
            if await send_breaking_alert(application, c):
                with session_scope() as s:
                    from .version import config_version, weights_version
                    s.add(DigestHistory(
                        digest_type="breaking", sent_at=int(time.time()),
                        post_ids=f"[\"{c.post_id}\"]", recipient="consumer",
                        config_version=config_version(), weights_version=weights_version(),
                    ))
                break

    morning_h, morning_m = map(int, cfg.digest.morning_local.split(":"))
    evening_h, evening_m = map(int, cfg.digest.evening_local.split(":"))
    op_h, op_m = map(int, cfg.operator.digest_local.split(":"))

    sched.add_job(_morning, CronTrigger(hour=morning_h, minute=morning_m, timezone=tz),
                  id="send_morning_digest")
    sched.add_job(_evening, CronTrigger(hour=evening_h, minute=evening_m, timezone=tz),
                  id="send_evening_digest")
    sched.add_job(_operator, CronTrigger(hour=op_h, minute=op_m, timezone=tz),
                  id="send_operator_digest")
    sched.add_job(_breaking_scan, IntervalTrigger(minutes=10), id="breaking_alert_scan")


def build_scheduler(telegram_app: "Application | None" = None) -> AsyncIOScheduler:
    cfg = load_config()
    sched = AsyncIOScheduler(timezone=secrets().tz)

    sched.add_job(
        _poll_job,
        IntervalTrigger(minutes=cfg.runtime.poll_interval_minutes),
        id="poll_subreddits",
        max_instances=1,
        coalesce=True,
        next_run_time=None,
    )

    sched.add_job(
        _heartbeat_check_job,
        IntervalTrigger(minutes=15),
        id="heartbeat_check",
        max_instances=1,
        coalesce=True,
    )

    smoke_h, smoke_m = map(int, cfg.watchdog.smoke_check_local.split(":"))
    sched.add_job(
        _smoke_check_job,
        CronTrigger(hour=smoke_h, minute=smoke_m, timezone=secrets().tz),
        id="daily_smoke_check",
    )

    sched.add_job(
        _baseline_job,
        CronTrigger(hour=3, minute=0, timezone=secrets().tz),
        id="compute_baselines",
    )

    sched.add_job(
        _expire_old_posts_job,
        CronTrigger(hour=4, minute=0, timezone=secrets().tz),
        id="expire_old_posts",
    )

    if telegram_app is not None:
        _make_digest_jobs(telegram_app, sched)

    return sched
