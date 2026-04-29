"""Message formatters. Abstracted so we could swap to email later with minimal diff."""

from __future__ import annotations

from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..ranker import Candidate

DISMISS_REASONS = [
    ("too_saturated", "Too saturated"),
    ("too_short", "Too short"),
    ("not_karen", "Not Karen-y"),
    ("off_brand", "Off-brand"),
    ("boring", "Boring"),
    ("other", "Other"),
]


def _why_summary(c: Candidate) -> str:
    bd = c.breakdown
    parts = []
    if bd["score_velocity_z"] > 1.0:
        parts.append(f"high score velocity (z={bd['score_velocity_z']:.1f})")
    if bd["comment_velocity_z"] > 1.0:
        parts.append(f"high comment velocity (z={bd['comment_velocity_z']:.1f})")
    if bd["keyword_boost"] >= 1.0:
        parts.append(f"strong keyword match ({bd['keyword_boost']:.1f})")
    if bd["saturation_term"] > 0:
        parts.append(f"saturation penalty ({bd['saturation_term']:.2f})")
    if not parts:
        parts.append("composite of weaker signals")
    return ", ".join(parts)


def consumer_digest_message(rank: int, c: Candidate, post_url: str) -> tuple[str, InlineKeyboardMarkup]:
    age_str = f"{c.age_h:.1f}h"
    duration = c.breakdown.get("duration_s")
    duration_str = f"{duration}s" if duration else "⏱ duration unknown"

    text = (
        f"🔥 #{rank} — r/{c.subreddit} · {age_str} old · score {c.latest_score} · {duration_str}\n\n"
        f"<b>{_escape(c.title)}</b>\n\n"
        f"<i>Why ranked:</i> {_why_summary(c)}\n\n"
        f"<a href=\"{post_url}\">Open on Reddit</a>"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Claim", callback_data=f"claim:{c.post_id}"),
        InlineKeyboardButton("❌ Dismiss", callback_data=f"dismiss:{c.post_id}"),
        InlineKeyboardButton("❓ Why", callback_data=f"why:{c.post_id}"),
    ]])
    return text, kb


def dismiss_reason_keyboard(post_id: str) -> InlineKeyboardMarkup:
    rows = []
    row: list[InlineKeyboardButton] = []
    for code, label in DISMISS_REASONS:
        row.append(InlineKeyboardButton(label, callback_data=f"reason:{post_id}:{code}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def why_breakdown(c: Candidate) -> str:
    bd = c.breakdown
    return (
        f"<b>Score breakdown for {c.post_id}</b>\n"
        f"  composite:           {c.composite:.3f}\n"
        f"  age:                 {c.age_h:.1f}h\n"
        f"  latest score:        {c.latest_score}\n"
        f"  score velocity:      {bd['score_velocity']:.1f}/h  (z={bd['score_velocity_z']:.2f})\n"
        f"  comment velocity:    {bd['comment_velocity']:.1f}/h  (z={bd['comment_velocity_z']:.2f})\n"
        f"  keyword boost:       {bd['keyword_boost']:.2f}\n"
        f"  subreddit weight:    {bd['subreddit_weight']:.2f}\n"
        f"  saturation term:     {bd['saturation_term']:.3f}\n"
        f"  duration:            {bd['duration_s']}\n"
    )


def operator_header(stats: dict) -> str:
    return (
        f"📊 Operator digest — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"  candidates after filters:  {stats.get('candidates', '?')}\n"
        f"  claims (7d):               {stats.get('claims_7d', '?')}\n"
        f"  dismissals (7d):           {stats.get('dismissals_7d', '?')}\n"
        f"  manual pickups (24h):      {stats.get('manual_pickups_24h', '?')}\n"
        f"  YouTube quota used today:  {stats.get('quota_used', '?')}\n"
        f"  cold-start subs:           {stats.get('cold_start_subs', '—')}\n"
    )


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
