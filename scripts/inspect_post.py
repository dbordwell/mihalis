#!/usr/bin/env python
"""Print a post + its full metric history. Phase 1 verification tool.

Usage:
    python scripts/inspect_post.py t3_abc123
    python scripts/inspect_post.py abc123          # short ID is auto-prefixed
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from karen_finder.db import session_scope
from karen_finder.models import Post, PostAction, PostMetric, SaturationCheck


def fmt_ts(unix: int) -> str:
    return datetime.fromtimestamp(unix, tz=timezone.utc).isoformat()


def main(post_id: str) -> int:
    if not post_id.startswith("t3_"):
        post_id = f"t3_{post_id}"

    with session_scope() as s:
        post = s.get(Post, post_id)
        if post is None:
            print(f"no post with id {post_id}")
            return 1

        print(f"=== {post.post_id} ===")
        print(f"  subreddit:       r/{post.subreddit}")
        print(f"  title:           {post.title}")
        print(f"  author:          {post.author}")
        print(f"  url:             {post.url}")
        print(f"  domain:          {post.domain}")
        print(f"  is_video:        {post.is_video}")
        print(f"  duration_s:      {post.duration_s}")
        print(f"  nsfw:            {post.nsfw}")
        print(f"  status:          {post.status}")
        print(f"  created:         {fmt_ts(post.created_utc)}")
        print(f"  first_seen:      {fmt_ts(post.first_seen_at)}")
        print(f"  last_updated:    {fmt_ts(post.last_updated_at)}")
        print(f"  also_seen_in:    {post.also_seen_in or '[]'}")
        print(f"  crosspost_parent:{post.crosspost_parent}")

        metrics = s.execute(
            select(PostMetric).where(PostMetric.post_id == post_id).order_by(PostMetric.observed_at)
        ).scalars().all()
        print(f"\n  metrics ({len(metrics)}):")
        print(f"    {'observed_at':<25} {'score':>7} {'comments':>9} {'upvote_ratio':>13}  in_rising")
        for m in metrics:
            print(
                f"    {fmt_ts(m.observed_at):<25} {m.score:>7d} {m.num_comments:>9d} "
                f"{(m.upvote_ratio or 0):>13.3f}  {m.in_rising}"
            )

        actions = s.execute(
            select(PostAction).where(PostAction.post_id == post_id).order_by(PostAction.acted_at)
        ).scalars().all()
        if actions:
            print(f"\n  actions ({len(actions)}):")
            for a in actions:
                print(f"    {fmt_ts(a.acted_at)}  {a.action:<14} reason={a.reason} cfg={a.config_version} weights={a.weights_version}")

        sat = s.execute(
            select(SaturationCheck).where(SaturationCheck.post_id == post_id).order_by(SaturationCheck.checked_at)
        ).scalars().all()
        if sat:
            print(f"\n  saturation_checks ({len(sat)}):")
            for c in sat:
                print(f"    {fmt_ts(c.checked_at)}  matches={c.matches_found} top_views={c.top_match_views} top_subs={c.top_match_channel_subs} would_drop={c.would_drop} in_effect={c.in_effect}")

    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: inspect_post.py <post_id>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
