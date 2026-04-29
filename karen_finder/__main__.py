"""Entry point. Starts health server + scheduler. Future phases add the Telegram bot."""

from __future__ import annotations

import asyncio
import signal

import click
import structlog

from .config import secrets
from .db import get_engine
from .healthcheck import run_health_server
from .ingestion import poll_with_lock
from .logging_setup import setup_logging
from .scheduler import build_scheduler

log = structlog.get_logger()


async def _serve() -> None:
    setup_logging()
    get_engine()  # initialize SQLite pragmas

    runner = await run_health_server()

    telegram_app = None
    if secrets().telegram_bot_token:
        from .telegram_bot import build_application
        telegram_app = build_application()
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling()
        log.info("telegram_bot_started")
    else:
        log.warning("telegram_bot_token_missing", note="running without Telegram surface")

    sched = build_scheduler(telegram_app=telegram_app)
    sched.start()
    log.info("scheduler_started", jobs=[j.id for j in sched.get_jobs()])

    # Run a poll immediately on startup so we don't wait the full interval for first data.
    loop = asyncio.get_running_loop()

    def _log_startup_poll(fut):
        if fut.exception() is not None:
            log.error("startup_poll_failed", error=str(fut.exception()))
        else:
            log.info("startup_poll_done", result=fut.result())

    fut = loop.run_in_executor(None, poll_with_lock)
    fut.add_done_callback(_log_startup_poll)

    stop_event = asyncio.Event()

    def _shutdown(*_):
        log.info("shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _shutdown())

    await stop_event.wait()

    log.info("shutting_down")
    sched.shutdown(wait=False)
    if telegram_app is not None:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()
    await runner.cleanup()
    # Brief grace window so litestream sidecar has time to ship final WAL frames.
    await asyncio.sleep(2)


@click.group()
def cli() -> None:
    """karen-finder CLI."""


@cli.command("serve")
def serve() -> None:
    """Run the long-lived service (default entry point)."""
    asyncio.run(_serve())


@cli.command("poll-once")
def poll_once_cmd() -> None:
    """Run one polling pass and exit. Useful for local sanity checks."""
    setup_logging()
    get_engine()
    summary = poll_with_lock()
    click.echo(summary)


def main() -> None:
    # When invoked as `python -m karen_finder` with no subcommand, run `serve`.
    import sys
    if len(sys.argv) == 1:
        sys.argv.append("serve")
    cli()


if __name__ == "__main__":
    main()
