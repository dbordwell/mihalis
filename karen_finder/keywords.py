"""Keyword tables + scoring. Tunable as code (small enough that a YAML wrapper is unnecessary).

Calibrated against the actual content style of the consumer's channel:
domestic/civic friction (HOA, parking, delivery, pets, yard, public spaces),
descriptive titles, family-friendly. See PLAN.md §11.
"""

from __future__ import annotations

# Drop on hit. Conservative — only true brand poison.
HARD_BLACKLIST: frozenset[str] = frozenset({
    "killed", "shot dead", "stabbed to death", "murder",
    "rape", "raped", "molest",
    # Slurs deliberately not enumerated here; expand from operator log review
    # rather than maintaining a public list of strings.
})

# Downrank significantly (-1.5 each). Context-dependent; not always off-brand.
SOFT_BLACKLIST: frozenset[str] = frozenset({
    "fight", "brawl", "punched", "drunk", "arrest", "arrested",
    "racist", "racism",
    "fuck", "fucking", "shit",
})

# +1.0 each, capped via ranker_caps.max_keyword_boost.
WHITELIST_STRONG: frozenset[str] = frozenset({
    "karen", "karens", "hoa",
    "freakout", "freak out", "loses it", "loses her",
    "entitled", "demanding", "complains", "complaining",
    "ruined", "ruins",
})

# +0.3 each. Situational triggers from his channel's actual content.
WHITELIST_CONTEXT: frozenset[str] = frozenset({
    "parking", "parked", "delivery", "package", "porch",
    "yard", "lawn", "plants", "garden", "flowers",
    "dog", "leash", "neighbor",
    "car seat", "child", "kids",
    "fishing", "beach", "pool", "gym", "store", "restaurant",
    "noise", "music", "fence", "property",
    "manager", "refund",
})


def keyword_boost(title: str, *, max_boost: float = 3.0) -> float | None:
    """Return additive keyword score, or None if a HARD_BLACKLIST term is present (drop).

    Multiple hits compound but the final boost is capped at `max_boost`.
    """
    if not title:
        return 0.0
    t = title.lower()
    if any(term in t for term in HARD_BLACKLIST):
        return None
    soft_hits = sum(1 for term in SOFT_BLACKLIST if term in t)
    strong_hits = sum(1 for term in WHITELIST_STRONG if term in t)
    context_hits = sum(1 for term in WHITELIST_CONTEXT if term in t)
    boost = (strong_hits * 1.0) + (context_hits * 0.3) + (soft_hits * -1.5)
    return min(boost, max_boost)
