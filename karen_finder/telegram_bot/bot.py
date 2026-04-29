"""Telegram bot wiring (PLAN.md §14, §15).

Single Application that handles both consumer and operator chats. Operator-only
commands check chat_id on each call. Consumer dismiss flow uses a 2-step button
sequence: dismiss → reason. Pasted Reddit URLs are captured as manual_pickup.
"""

from __future__ import annotations

import json
import re
import time

import structlog
from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)

from ..config import load_config, secrets
from ..db import session_scope
from ..models import DigestHistory, Post, PostAction, PostMetric
from ..ranker import Candidate, score_candidates
from ..reddit_client import canonicalize_url, get_submission_by_short_id
from ..version import config_version, weights_version
from .formatters import (
    consumer_digest_message, dismiss_reason_keyboard, operator_header, why_breakdown,
)

log = structlog.get_logger()
REDDIT_URL_RE = re.compile(r"https?://(?:www\.|old\.)?reddit\.com/r/\w+/comments/(\w+)/?", re.I)


# ---------- helpers ----------

def _allowed_chat_ids() -> set[str]:
    s = secrets()
    return {str(x) for x in (s.telegram_consumer_chat_id, s.telegram_operator_chat_id) if x}


def _gate(handler):
    """Reject unknown chat_ids silently. /start is exempt so chat IDs can be discovered."""
    async def wrapped(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        allowed = _allowed_chat_ids()
        chat_id = str(update.effective_chat.id)
        if allowed and chat_id not in allowed:
            log.warning("unauthorized_chat_blocked", chat_id=chat_id, handler=handler.__name__)
            return  # silent — do not reveal the bot's purpose to randoms
        return await handler(update, ctx)
    wrapped.__name__ = handler.__name__
    return wrapped


def _is_operator(update: Update) -> bool:
    op_id = secrets().telegram_operator_chat_id
    return op_id and str(update.effective_chat.id) == str(op_id)


def _post_url(post_id: str) -> str:
    raw = post_id.removeprefix("t3_")
    return f"https://www.reddit.com/comments/{raw}"


def _record_action(post_id: str, action: str, reason: str | None = None) -> None:
    with session_scope() as s:
        s.add(PostAction(
            post_id=post_id, action=action, reason=reason,
            config_version=config_version(),
            weights_version=weights_version(),
            acted_at=int(time.time()),
        ))


def _set_status(post_id: str, status: str) -> None:
    with session_scope() as s:
        post = s.get(Post, post_id)
        if post is not None:
            post.status = status
            post.last_updated_at = int(time.time())


def _candidate_by_post_id(post_id: str) -> Candidate | None:
    for c in score_candidates():
        if c.post_id == post_id:
            return c
    return None


# ---------- command handlers ----------

async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"Karen-Finder online. Your chat ID is <code>{chat_id}</code>. "
        f"If you are the operator setting up secrets, copy this into TELEGRAM_OPERATOR_CHAT_ID; "
        f"if you are the consumer, into TELEGRAM_CONSUMER_CHAT_ID.",
        parse_mode=ParseMode.HTML,
    )


@_gate
async def cmd_digest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-send today's most recent digest by re-running the ranker."""
    cfg = load_config()
    candidates = score_candidates()[: cfg.digest.size]
    if not candidates:
        await update.message.reply_text("no candidates pass filters right now.")
        return
    for i, c in enumerate(candidates, 1):
        text, kb = consumer_digest_message(i, c, _post_url(c.post_id))
        await ctx.bot.send_message(
            chat_id=update.effective_chat.id, text=text,
            parse_mode=ParseMode.HTML, disable_web_page_preview=False, reply_markup=kb,
        )


@_gate
async def cmd_stats(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    since = int(time.time()) - 7 * 86400
    with session_scope() as s:
        rows = s.execute(
            select(PostAction.action).where(PostAction.acted_at >= since)
        ).all()
    counts = {"claim": 0, "dismiss": 0, "open": 0, "manual_pickup": 0}
    for (action,) in rows:
        counts[action] = counts.get(action, 0) + 1
    msg = "\n".join(f"  {k}: {v}" for k, v in counts.items())
    await update.message.reply_text(f"Last 7 days:\n{msg}")


@_gate
async def cmd_why(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = (update.message.text or "").split()
    if len(args) < 2:
        await update.message.reply_text("usage: /why <post_id>")
        return
    pid = args[1]
    if not pid.startswith("t3_"):
        pid = f"t3_{pid}"
    c = _candidate_by_post_id(pid)
    if c is None:
        await update.message.reply_text(f"{pid} is not in the current candidate set.")
        return
    await update.message.reply_text(why_breakdown(c), parse_mode=ParseMode.HTML)


@_gate
async def cmd_inspect(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_operator(update):
        return
    args = (update.message.text or "").split()
    if len(args) < 2:
        await update.message.reply_text("usage: /inspect <post_id>")
        return
    pid = args[1]
    if not pid.startswith("t3_"):
        pid = f"t3_{pid}"
    with session_scope() as s:
        post = s.get(Post, pid)
        if post is None:
            await update.message.reply_text(f"{pid} not found")
            return
        m_count = s.execute(
            select(PostMetric).where(PostMetric.post_id == pid)
        ).scalars().all()
    await update.message.reply_text(
        f"<b>{pid}</b>\n"
        f"  r/{post.subreddit} · status={post.status} · age={(int(time.time())-post.created_utc)//3600}h\n"
        f"  title: {post.title}\n"
        f"  url: {post.url}\n"
        f"  duration: {post.duration_s} · is_video: {post.is_video} · nsfw: {post.nsfw}\n"
        f"  metric observations: {len(m_count)}",
        parse_mode=ParseMode.HTML,
    )


@_gate
async def cmd_quota(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_operator(update):
        return
    from ..models import SystemState
    with session_scope() as s:
        used = s.get(SystemState, "youtube_quota_used_today")
        date = s.get(SystemState, "youtube_quota_date")
    used_str = used.value if used else "0"
    date_str = date.value if date else "—"
    await update.message.reply_text(f"YouTube quota used: {used_str}/10000 (date {date_str})")


@_gate
async def cmd_health(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_operator(update):
        return
    from ..models import SystemState
    with session_scope() as s:
        last = s.get(SystemState, "last_poll_ok_at")
    last_str = last.value if last else "never"
    await update.message.reply_text(f"last_poll_ok_at = {last_str}")


# ---------- callback handlers ----------

@_gate
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data.startswith("claim:"):
        post_id = data[len("claim:"):]
        _record_action(post_id, "claim")
        _set_status(post_id, "claimed")
        await q.edit_message_reply_markup(reply_markup=None)
        await q.edit_message_text(text=q.message.text_html + "\n\n✅ <b>Claimed</b>", parse_mode=ParseMode.HTML)

    elif data.startswith("dismiss:"):
        post_id = data[len("dismiss:"):]
        _record_action(post_id, "dismiss")
        _set_status(post_id, "dismissed")
        await q.edit_message_reply_markup(reply_markup=dismiss_reason_keyboard(post_id))

    elif data.startswith("reason:"):
        _, post_id, reason = data.split(":", 2)
        # Update the most recent dismiss action for this post with the reason.
        with session_scope() as s:
            pa = s.execute(
                select(PostAction)
                .where(PostAction.post_id == post_id, PostAction.action == "dismiss")
                .order_by(PostAction.acted_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if pa is not None and pa.reason is None:
                pa.reason = reason
        await q.edit_message_reply_markup(reply_markup=None)
        striked = "\n".join(f"<s>{line}</s>" for line in (q.message.text_html or "").splitlines())
        await q.edit_message_text(text=f"{striked}\n\n❌ Dismissed ({reason})", parse_mode=ParseMode.HTML)

    elif data.startswith("why:"):
        post_id = data[len("why:"):]
        c = _candidate_by_post_id(post_id)
        if c is None:
            await ctx.bot.send_message(chat_id=q.message.chat_id, text=f"{post_id} no longer in candidate set.")
            return
        await ctx.bot.send_message(
            chat_id=q.message.chat_id, text=why_breakdown(c), parse_mode=ParseMode.HTML,
        )


# ---------- url ingestion (manual_pickup) ----------

@_gate
async def on_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    match = REDDIT_URL_RE.search(text)
    if not match:
        return
    short_id = match.group(1)
    post_id = f"t3_{short_id}"

    # Lazy ingest if not in DB.
    with session_scope() as s:
        existing = s.get(Post, post_id)
    if existing is None:
        try:
            sub = get_submission_by_short_id(short_id)
            if sub is None:
                await update.message.reply_text(f"could not find {post_id} on Reddit")
                return
            now = int(time.time())
            with session_scope() as s:
                s.add(Post(
                    post_id=post_id,
                    subreddit=sub.subreddit.display_name,
                    title=sub.title,
                    author=str(sub.author) if sub.author else None,
                    url=canonicalize_url(sub.url or ""),
                    domain=sub.domain,
                    created_utc=int(sub.created_utc),
                    is_video=bool(sub.is_video),
                    is_self=bool(sub.is_self),
                    nsfw=bool(sub.over_18),
                    duration_s=None,
                    crosspost_parent=None,
                    also_seen_in=None,
                    status="new",
                    first_seen_at=now,
                    last_updated_at=now,
                ))
                s.add(PostMetric(
                    post_id=post_id, observed_at=now,
                    score=sub.score, num_comments=sub.num_comments,
                    upvote_ratio=sub.upvote_ratio, in_rising=False,
                ))
        except Exception as e:
            await update.message.reply_text(f"could not ingest {post_id}: {e}")
            return

    _record_action(post_id, "manual_pickup")
    await update.message.reply_text(
        f"Logged {post_id} as manual_pickup. Operator review will check why this didn't surface."
    )


# ---------- digest senders (called from scheduler jobs) ----------

async def send_consumer_digest(application: Application, *, digest_type: str) -> int:
    cfg = load_config()
    chat_id = secrets().telegram_consumer_chat_id
    if not chat_id:
        log.warning("consumer_chat_id_not_set")
        return 0
    candidates = score_candidates()[: cfg.digest.size]
    if not candidates and not cfg.digest.send_empty_if_nothing_qualifies:
        log.info("digest_skipped_empty", digest_type=digest_type)
        return 0
    sent = 0
    for i, c in enumerate(candidates, 1):
        text, kb = consumer_digest_message(i, c, _post_url(c.post_id))
        await application.bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode=ParseMode.HTML, disable_web_page_preview=False, reply_markup=kb,
        )
        sent += 1
    with session_scope() as s:
        s.add(DigestHistory(
            digest_type=digest_type, sent_at=int(time.time()),
            post_ids=json.dumps([c.post_id for c in candidates]),
            recipient="consumer", config_version=config_version(),
            weights_version=weights_version(),
        ))
    # mark posts as 'shown' so they aren't re-sent
    with session_scope() as s:
        for c in candidates:
            p = s.get(Post, c.post_id)
            if p and p.status == "new":
                p.status = "shown"
    return sent


async def send_operator_digest(application: Application) -> int:
    cfg = load_config()
    chat_id = secrets().telegram_operator_chat_id
    if not chat_id:
        log.warning("operator_chat_id_not_set")
        return 0
    candidates = score_candidates()[: cfg.operator.size]
    since_7d = int(time.time()) - 7 * 86400
    since_24h = int(time.time()) - 86400
    with session_scope() as s:
        actions_7d = s.execute(
            select(PostAction.action).where(PostAction.acted_at >= since_7d)
        ).all()
        manual_24h = s.execute(
            select(PostAction).where(
                PostAction.acted_at >= since_24h, PostAction.action == "manual_pickup",
            )
        ).scalars().all()
        from ..models import SystemState
        quota_row = s.get(SystemState, "youtube_quota_used_today")
    counts = {"claim": 0, "dismiss": 0, "manual_pickup": 0}
    for (a,) in actions_7d:
        counts[a] = counts.get(a, 0) + 1
    header = operator_header({
        "candidates": len(candidates),
        "claims_7d": counts.get("claim", 0),
        "dismissals_7d": counts.get("dismiss", 0),
        "manual_pickups_24h": len(manual_24h),
        "quota_used": quota_row.value if quota_row else "0",
    })
    await application.bot.send_message(chat_id=chat_id, text=header, parse_mode=ParseMode.HTML)
    for i, c in enumerate(candidates, 1):
        text, kb = consumer_digest_message(i, c, _post_url(c.post_id))
        await application.bot.send_message(
            chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
            disable_web_page_preview=True, reply_markup=kb,
        )
    return len(candidates)


async def send_breaking_alert(application: Application, candidate: Candidate) -> bool:
    chat_id = secrets().telegram_consumer_chat_id
    if not chat_id:
        return False
    text, kb = consumer_digest_message(0, candidate, _post_url(candidate.post_id))
    text = "🚨 BREAKING — high-velocity post detected\n\n" + text
    await application.bot.send_message(
        chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=kb,
    )
    return True


# ---------- builder ----------

def build_application() -> Application:
    token = secrets().telegram_bot_token
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("why", cmd_why))
    app.add_handler(CommandHandler("inspect", cmd_inspect))
    app.add_handler(CommandHandler("quota", cmd_quota))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message))
    return app
