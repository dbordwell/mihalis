#!/usr/bin/env python
"""Run the ranker and print a top-N digest. No Telegram side-effects.

Usage:
    python scripts/manual_digest.py [N]    # default N = 10
"""
from __future__ import annotations

import sys

from karen_finder.ranker import score_candidates


def main(n: int) -> int:
    candidates = score_candidates()
    if not candidates:
        print("no candidates passed filters")
        return 0
    print(f"=== top {min(n, len(candidates))} of {len(candidates)} candidates ===\n")
    for i, c in enumerate(candidates[:n], 1):
        print(f"#{i:2d}  composite={c.composite:7.2f}  r/{c.subreddit:<22}  age={c.age_h:5.1f}h  score={c.latest_score:>5}")
        print(f"      title:    {c.title}")
        bd = c.breakdown
        print(
            f"      sv_z={bd['score_velocity_z']:.2f}  cv_z={bd['comment_velocity_z']:.2f}  "
            f"kw={bd['keyword_boost']:.2f}  sub_w={bd['subreddit_weight']:.2f}  "
            f"sat={bd['saturation_term']:.3f}  dur={bd['duration_s']}"
        )
        print()
    return 0


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    sys.exit(main(n))
