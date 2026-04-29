"""Single aiohttp route at /health — Fly's liveness probe target.

Returns 200 if `last_poll_ok_at` is younger than `heartbeat_max_age_minutes`,
503 otherwise. This is the entire HTTP surface; do not add more routes.
"""

from __future__ import annotations

import time

import structlog
from aiohttp import web
from sqlalchemy import select

from .config import load_config
from .db import session_scope
from .models import SystemState

log = structlog.get_logger()


async def health(_request: web.Request) -> web.Response:
    cfg = load_config()
    threshold = cfg.watchdog.heartbeat_max_age_minutes * 60
    now = int(time.time())

    with session_scope() as s:
        row = s.execute(select(SystemState).where(SystemState.key == "last_poll_ok_at")).scalar_one_or_none()

    if row is None:
        # Just-started container: give it a grace window equal to the threshold.
        return web.json_response({"status": "starting", "last_poll_ok_at": None}, status=200)

    last = int(row.value)
    age = now - last
    if age > threshold:
        return web.json_response(
            {"status": "stale", "last_poll_ok_at": last, "age_seconds": age, "threshold_seconds": threshold},
            status=503,
        )
    return web.json_response({"status": "ok", "last_poll_ok_at": last, "age_seconds": age}, status=200)


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    return app


async def run_health_server(host: str = "0.0.0.0", port: int = 8080) -> web.AppRunner:
    runner = web.AppRunner(make_app())
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("health_server_started", host=host, port=port)
    return runner
