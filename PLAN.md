# Karen-Finder v1 — Reddit Discovery Pipeline

## 0. Read this first

You are building a personal-use Reddit discovery pipeline that surfaces trending video posts to a single end-user. The end-user (the **consumer**) currently spends hours per day manually browsing for relevant clips. This system compresses that to a 10-minute review of two daily ranked digests plus occasional breaking alerts.

**Build philosophy:** write all the code in one push; phase the *validation*, not the build. The system has nothing useful to *say* until ~7 days of metric history accumulate, but it can be code-complete on day one. See §16 for the validation timeline.

**Scope discipline:** §21 lists what is explicitly out of v1. If you find yourself reaching for any of it, stop and re-read §21.

---

## 1. What we are building

A Python service that:

1. Polls a configurable set of subreddits every 10 minutes.
2. Stores every video post and a time-series of its metrics (score, comments, upvote ratio).
3. Computes velocity and acceleration per post, z-scored against rolling subreddit baselines.
4. Filters by keyword whitelist/blacklist and hard rules (age, score floor, must be video, duration band, brand-safety blocklist).
5. Ranks surviving posts by a weighted composite score.
6. Runs a saturation check against the YouTube Data API (title-similarity search) — in *shadow mode* for the first week, then live.
7. Delivers two daily digests (9 a.m. and 7 p.m. EST) to the consumer via Telegram, plus breaking alerts for outlier posts (capped at 3/day).
8. Tracks consumer actions (claim, dismiss + reason, open, ignore, manual pickup) for tuning.
9. Delivers a parallel **operator digest** to us, with metrics, weight-sensitivity output, and errors.
10. Self-monitors with a three-layer watchdog (process / heartbeat / output).

That is all v1 does.

---

## 2. Goals and non-goals

### Goals (v1)
- Replace manual subreddit browsing with two daily digests + breaking alerts.
- Avoid surfacing clips that have already been heavily reused on YouTube — the saturation check downranks posts where a similar video has many views on a high-subscriber channel, since reusing them would be flagged as non-original by YouTube.
- Tunable weights, thresholds, and keyword lists via config files (no code changes for tuning).
- Persistent metric history so we can re-tune retroactively.
- Operator-side observability sufficient to debug quality issues without requiring consumer feedback first.
- Run on a single ~€4/mo Hetzner CX22 VPS.

### Non-goals (v1)
- Cross-platform (TikTok, IG, Twitter) — deferred to v2.
- Perceptual hashing or audio fingerprinting — deferred (saturation roadmap §13.6).
- LLM-based classification — deferred.
- Whisper transcription — deferred.
- Auto-editing, auto-thumbnail, auto-publish — different stage of pipeline entirely.
- Web UI / dashboard — Telegram + CLI is the v1 interface.
- Multi-creator support — single-consumer only.
- Auto-pruning of underperforming subreddits — flag in operator report only; the consumer's call.

---

## 3. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Library support |
| Reddit API | PRAW | Official, sync is fine at 1 poller |
| YouTube API | google-api-python-client | Saturation check |
| Database | SQLite | One writer (the poller); concurrency is not a real concern |
| ORM | SQLAlchemy 2.x | Productivity > raw SQL |
| Migrations | Alembic | Standard with SQLAlchemy |
| Scheduler | APScheduler | In-process, no external broker |
| Telegram | python-telegram-bot v20+ | Async, inline keyboards, callbacks |
| Config | YAML + pydantic-settings | Tunable without redeploy |
| String similarity | rapidfuzz | Saturation title matching |
| Logging | structlog | JSON logs make debugging velocity bugs easy |
| Deployment | Docker on Fly.io | Free tier covers our load; one-command deploy; portable if we ever move |
| Data durability | Litestream → Cloudflare R2 | Continuous SQLite WAL replication to off-platform object storage |
| Healthcheck | aiohttp single `/health` route | Liveness probe for Fly's platform watchdog; **not** a web app |
| Time zone | America/New_York | Consumer is EST/EDT |

**Do not introduce:** Celery, Redis, Kafka, RabbitMQ, FastAPI, Flask, Postgres, Docker Compose, Kubernetes, any frontend framework. If you think you need any of these, you're out of scope. **Exception:** a single `aiohttp` route at `/health` is allowed for Fly's liveness probe — this is a health endpoint, not a web application. Do not add additional routes.

---

## 4. Two-user model (architectural, not optional)

The system has two distinct users with non-overlapping needs. **Do not blur them.**

| | Consumer | Operator |
|---|---|---|
| Who | The YouTube creator | Us |
| OS | Windows | Linux VPS |
| Surface | Telegram only | CLI, SQL views, parallel Telegram channel |
| Cares about | Good clips, instant feedback | Calibration, debugging, system health |
| Frequency | 2x daily digests + alerts | Daily operator digest, on-demand queries |
| Failure tolerance | Zero — janky day-1 kills adoption | High — we expect to debug |

The consumer surface is **polished, conservative, sparse**. The operator surface is **noisy, rich, diagnostic**. Different design priorities.

---

## 5. Project structure

```
karen-finder/
├── PLAN.md                  # This file
├── CLAUDE.md                # Orientation for Claude Code sessions
├── README.md                # Operator setup/run guide
├── BACKLOG.md               # v2+ ideas (do not start without explicit decision)
├── pyproject.toml
├── .env.example
├── config.yaml              # Tunable weights, subs, thresholds
├── alembic.ini
├── alembic/
│   └── versions/
├── karen_finder/
│   ├── __init__.py
│   ├── config.py            # pydantic-settings + YAML loader
│   ├── db.py                # SQLAlchemy setup
│   ├── models.py            # ORM models
│   ├── reddit_client.py     # PRAW wrapper
│   ├── ingestion.py         # Poll + repoll job
│   ├── velocity.py          # Pure functions: velocity, acceleration, z-score
│   ├── baselines.py         # Nightly baseline computation
│   ├── keywords.py          # Whitelist/blacklist tables + scoring fn
│   ├── saturation.py        # YouTube saturation check (v1.0 title search)
│   ├── ranker.py            # Hard filters + composite score
│   ├── telegram_bot/
│   │   ├── __init__.py
│   │   ├── consumer.py      # Consumer-facing handlers, commands, buttons
│   │   ├── operator.py      # Operator digest sender
│   │   └── formatters.py    # Message formatting (abstract — swappable to email)
│   ├── scheduler.py         # APScheduler job wiring
│   ├── watchdog.py          # Heartbeat + output checks
│   └── version.py           # config_version, weights_version stamping
├── scripts/
│   ├── inspect_post.py      # Post + metric history dump
│   ├── manual_digest.py     # Run the ranker without sending Telegram
│   ├── analyze.py           # SQL views: hit rates, weight sensitivity, etc.
│   ├── tune_weights.py      # Re-rank historical candidates with proposed weights
│   └── deploy.sh            # rsync + systemctl restart
├── tests/
│   ├── test_velocity.py
│   ├── test_keywords.py
│   ├── test_ranker.py
│   ├── test_saturation.py
│   └── fixtures/
├── Dockerfile               # Multi-stage build; final image <200MB
├── .dockerignore
├── fly.toml                 # Fly.io app config (region, volume mount, healthcheck)
├── litestream.yml           # SQLite WAL replication → R2
└── entrypoint.sh            # 1) litestream restore (if no local DB) 2) litestream replicate (bg) 3) exec python
```

---

## 6. Database schema (SQLite)

Migrations via Alembic. Initial migration creates everything below.

### `posts`
```
post_id           TEXT PRIMARY KEY     -- Reddit fullname, e.g. t3_abc123
subreddit         TEXT NOT NULL
title             TEXT NOT NULL
author            TEXT
url               TEXT NOT NULL        -- canonicalized
domain            TEXT
created_utc       INTEGER NOT NULL
is_video          BOOLEAN NOT NULL
is_self           BOOLEAN NOT NULL
nsfw              BOOLEAN NOT NULL
duration_s        INTEGER              -- nullable; only Reddit-hosted reliably has this
crosspost_parent  TEXT                 -- post_id of original if crosspost
also_seen_in      TEXT                 -- JSON array of subreddit names
status            TEXT NOT NULL DEFAULT 'new'   -- new | shown | claimed | dismissed | expired
first_seen_at     INTEGER NOT NULL
last_updated_at   INTEGER NOT NULL
INDEX(subreddit, created_utc)
INDEX(status, created_utc)
INDEX(url)
```

### `post_metrics` (time-series)
```
id            INTEGER PRIMARY KEY AUTOINCREMENT
post_id       TEXT NOT NULL REFERENCES posts(post_id)
observed_at   INTEGER NOT NULL
score         INTEGER NOT NULL
num_comments INTEGER NOT NULL
upvote_ratio REAL
in_rising    BOOLEAN NOT NULL DEFAULT 0
UNIQUE(post_id, observed_at)
INDEX(post_id, observed_at DESC)
```

### `subreddit_baselines` (rolling stats)
```
subreddit       TEXT NOT NULL
age_bucket      INTEGER NOT NULL     -- one of: 1, 2, 4, 8, 12, 24 (hours)
metric          TEXT NOT NULL        -- score_velocity | comment_velocity | acceleration
mean            REAL NOT NULL
stdev           REAL NOT NULL
sample_count    INTEGER NOT NULL
computed_at     INTEGER NOT NULL
PRIMARY KEY(subreddit, age_bucket, metric)
```

### `subreddits` (config registry)
```
name           TEXT PRIMARY KEY
enabled        BOOLEAN NOT NULL DEFAULT 1
weight         REAL NOT NULL DEFAULT 1.0
disabled_reason TEXT
last_polled_at INTEGER
```

### `post_actions` (consumer feedback ledger)
```
id              INTEGER PRIMARY KEY AUTOINCREMENT
post_id         TEXT NOT NULL REFERENCES posts(post_id)
action          TEXT NOT NULL    -- claim | dismiss | open | ignore | manual_pickup
reason          TEXT             -- on dismiss only: too_saturated | too_short | not_karen | off_brand | boring | other
config_version  TEXT NOT NULL    -- git short SHA at action time
weights_version TEXT NOT NULL    -- hash of relevant config.yaml weights at action time
acted_at        INTEGER NOT NULL
INDEX(post_id, acted_at)
INDEX(action, acted_at)
```

### `saturation_checks` (shadow mode log + live decisions)
```
id                       INTEGER PRIMARY KEY AUTOINCREMENT
post_id                  TEXT NOT NULL REFERENCES posts(post_id)
checked_at               INTEGER NOT NULL
matches_found            INTEGER NOT NULL
top_match_views          INTEGER
top_match_channel_subs   INTEGER
top_match_url            TEXT
top_match_title_similarity REAL
would_drop               BOOLEAN NOT NULL    -- hard filter would have triggered
would_downrank_score     REAL NOT NULL       -- the negative ranker term
in_effect                BOOLEAN NOT NULL    -- false during shadow week
INDEX(post_id, checked_at)
```

### `digest_history`
```
id           INTEGER PRIMARY KEY AUTOINCREMENT
digest_type  TEXT NOT NULL    -- daily_morning | daily_evening | breaking | operator
sent_at      INTEGER NOT NULL
post_ids     TEXT NOT NULL    -- JSON list
recipient    TEXT NOT NULL    -- consumer | operator
config_version  TEXT NOT NULL
weights_version TEXT NOT NULL
```

### `system_state` (key/value heartbeat + counters)
```
key    TEXT PRIMARY KEY
value  TEXT NOT NULL
updated_at INTEGER NOT NULL
```
Used keys: `last_poll_ok_at`, `last_baseline_ok_at`, `last_digest_ok_at`, `youtube_quota_used_today`, `youtube_quota_reset_at`.

---

## 7. Configuration

### Secrets

`.env` is for **local development only**. In production these are set via `flyctl secrets set` and injected as environment variables into the container. Never commit `.env`.

```
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=karen-finder/0.1 by u/<username>
YOUTUBE_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CONSUMER_CHAT_ID=
TELEGRAM_OPERATOR_CHAT_ID=
DATABASE_URL=sqlite:////data/karen_finder.db   # /data is the Fly volume mount
LOG_LEVEL=INFO
TZ=America/New_York

# Litestream → Cloudflare R2
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=karen-finder-backup
R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
```

### `config.yaml` (tunable, committed)
```yaml
runtime:
  poll_interval_minutes: 10
  metric_repoll_max_age_h: 48
  min_age_hours: 1.0
  max_age_hours: 72
  min_score: 50
  min_seconds: 40
  max_seconds: 180
  block_nsfw: true
  baseline_warmup_days: 7

subreddits:
  # Tier 1 — Karen-specific, video-heavy, brand-safe (HIGH WEIGHT)
  - name: Karens
    weight: 1.5
  - name: Karensinthewild
    weight: 1.5
  - name: FuckYouKaren
    weight: 1.4
  - name: EntitledKaren
    weight: 1.3
  # Tier 1 — supporting, brand-safe
  - name: PublicFreakout
    weight: 1.0
  - name: instant_regret
    weight: 1.0
  - name: IdiotsInCars
    weight: 1.0
  - name: byebyejob
    weight: 1.0
  - name: ConvenientCop
    weight: 0.9
  # Tier 2 — text-heavy but occasional video gold (LOW WEIGHT)
  - name: EntitledPeople
    weight: 0.6
  - name: IDontWorkHereLady
    weight: 0.6

allowed_domains:
  - v.redd.it
  - youtube.com
  - youtu.be
  - streamable.com
  - i.imgur.com    # imgur sometimes hosts video
  - imgur.com
  - gfycat.com

ranker_weights:
  score_velocity_z:   1.0
  comment_velocity_z: 0.8
  acceleration_z:     0.6
  keyword_boost:      2.0
  subreddit_weight:   1.0
  saturation_penalty: 1.5    # weight applied to the negative saturation term

ranker_caps:
  max_keyword_boost: 3.0     # prevents runaway when many whitelist terms hit

digest:
  morning_local: "09:00"
  evening_local: "19:00"
  size: 10
  send_empty_if_nothing_qualifies: false   # no padding; better to send nothing

breaking_alerts:
  enabled: true
  z_score_threshold: 4.0     # start conservative; tune down based on data
  max_age_hours: 6
  daily_cap: 3
  quiet_hours_start_local: "00:00"
  quiet_hours_end_local: "06:00"
  # alerts during quiet hours are suppressed but rolled into morning digest under "while you slept"

saturation:
  mode: shadow                # shadow | active
  lookback_days: 30
  title_similarity_threshold: 0.6
  drop_if_views_above: 100000
  drop_if_channel_subs_above: 50000
  downrank_per_1k_views: 0.001
  top_n_to_check: 30          # only check top N candidates by pre-saturation score

operator:
  digest_local: "08:30"       # 30 min before consumer's morning digest
  size: 30                    # operator sees more candidates than consumer

watchdog:
  heartbeat_max_age_minutes: 25
  smoke_check_local: "11:00"
```

---

## 8. Reddit setup (one-time, document in README)

1. Sign in to reddit.com → preferences → apps → "create another app".
2. Type: **script**. Redirect URI: `http://localhost:8080` (unused for script apps).
3. Copy client ID (under the app name) and secret into `.env`.
4. User agent must be unique and identify the app (format in `.env.example`).
5. PRAW is read-only here. No write scopes.

---

## 9. Ingestion

### What runs every 10 minutes

For each enabled subreddit:
1. Fetch `/new` (limit 50) → upsert into `posts`, insert `post_metrics` row with `in_rising=False`.
2. Fetch `/rising` (limit 25) → upsert into `posts`, insert/update `post_metrics` row with `in_rising=True`.
3. For all posts in DB with `created_utc > now() - metric_repoll_max_age_h` AND `status IN ('new', 'shown')`: fetch fresh metrics in batches of 100 IDs via `reddit.info(fullnames=...)` and append to `post_metrics`.

### Filtering at ingestion (drop before insert)

A post is dropped immediately if:
- Not a video (no `is_video` AND domain not in `allowed_domains`).
- NSFW and `block_nsfw=true`.
- Crosspost where the original is already in our DB → dedupe by canonicalized URL, append the new subreddit to `also_seen_in` on the original row, do not create a duplicate post row.
- Author is `[deleted]` AND `removed_by_category` is set (the post is dead — not worth tracking).

### URL canonicalization

Strip query params except `v=` for YouTube. Lowercase host. Strip trailing slashes. Used for crosspost dedup.

### Crosspost cycle handling

A video can be crossposted r/A → r/B → r/C. Walk `crosspost_parent` until you find the original or hit depth 3. If a cycle, log warning and treat the deepest discovered as the original.

### Concurrency

Single poller process. Use a file-based lock (`fcntl.flock`) at the top of the poll job — SQLite + one process means a Postgres advisory lock is overkill. Just guarantee two cron firings cannot overlap.

### Rate limits

PRAW handles 429s automatically. Don't run more than one poller. If `subreddit.new()` raises `Forbidden` or `NotFound`, set `subreddits.enabled = false` and `disabled_reason`, and post to the operator Telegram.

### Acceptance criteria for ingestion alone

- Run for 24h on configured subs → `post_metrics` shows steadily growing rows for active posts (multiple observations each).
- No duplicate `(post_id, observed_at)` rows.
- No PRAW rate-limit errors in logs.
- `scripts/inspect_post.py <post_id>` prints a coherent picture: post row + metrics time-series.
- Crossposts dedupe correctly; `also_seen_in` populates.

---

## 10. Velocity & baselines

### Per-post derived metrics

Pull the latest two `post_metrics` rows. Let `Δt = observed_at_2 - observed_at_1` (seconds).

```python
score_velocity      = (score_2 - score_1) / max(Δt, 1) * 3600          # per hour
comment_velocity    = (num_comments_2 - num_comments_1) / max(Δt, 1) * 3600
# acceleration: change in velocity over the window, requires latest 3 rows
acceleration        = (velocity_recent - velocity_prior) / max(Δt, 1) * 3600
age_hours           = (now - created_utc) / 3600
```

### Z-score against baseline

Bucket the post's age into one of `{1, 2, 4, 8, 12, 24}` (round down). Look up the row in `subreddit_baselines` for `(subreddit, age_bucket, metric)`.

```python
z = (raw_value - baseline.mean) / max(baseline.stdev, 1e-6)
```

If `baseline.sample_count < 30` or no row exists → fall back (see §10.3 cold-start).

### Baseline computation (nightly job, 03:00 EST)

For each enabled subreddit and each age bucket `{1, 2, 4, 8, 12, 24}`:
1. Query all `post_metrics` rows where the post's age at `observed_at` falls in the bucket, over the last 14 days.
2. Compute mean and stdev of `score_velocity`, `comment_velocity`, `acceleration` for those rows.
3. Upsert into `subreddit_baselines`.

### Cold-start fallback

If `now() - earliest_baseline_computed_at < baseline_warmup_days` OR the relevant baseline row has `sample_count < 30`:
- Replace z-scores with raw values normalized by per-subreddit medians from the last 7 days (or all data if shorter).
- Ranker still produces an ordering; just less calibrated.
- Operator digest annotates "cold-start fallback active for sub X" until baselines warm up.

---

## 11. Keyword filter

Keyword tables live in `keywords.py`. Tunable; treat as config that needs code review (small enough that a YAML file is unnecessary churn).

```python
HARD_BLACKLIST = {
    # Drop on hit. Conservative — only true brand poison.
    # Racial/ethnic slurs (curated list, not enumerated here).
    "killed", "shot dead", "stabbed to death", "murder",
    "rape", "raped", "molest",
    # Add more as they come up — operator log review will surface them.
}

SOFT_BLACKLIST = {
    # Downrank significantly (-1.5). Context-dependent; not all are off-brand.
    "fight", "brawl", "punched", "drunk", "arrest", "arrested",
    "racist", "racism",     # too edgy for his audience by default
    # Profanity in *title* — Reddit uses it casually, but he avoids in his own titles.
    "fuck", "fucking", "shit",
}

WHITELIST_STRONG = {
    # +1.0 each, capped via ranker_caps.max_keyword_boost.
    "karen", "karens", "hoa",
    "freakout", "freak out", "loses it", "loses her",
    "entitled", "demanding", "complains", "complaining",
    "ruined", "ruins",
}

WHITELIST_CONTEXT = {
    # +0.3 each. Situational triggers from his channel's actual content.
    "parking", "parked", "delivery", "package", "porch",
    "yard", "lawn", "plants", "garden", "flowers",
    "dog", "leash", "neighbor",
    "car seat", "child", "kids",
    "fishing", "beach", "pool", "gym", "store", "restaurant",
    "noise", "music", "fence", "property",
    "manager", "refund",
}
```

### Scoring

```python
def keyword_boost(title: str) -> float | None:
    t = title.lower()
    if any(term in t for term in HARD_BLACKLIST):
        return None  # drop
    soft_hits   = sum(1 for term in SOFT_BLACKLIST if term in t)
    strong_hits = sum(1 for term in WHITELIST_STRONG if term in t)
    context_hits = sum(1 for term in WHITELIST_CONTEXT if term in t)
    boost = (strong_hits * 1.0) + (context_hits * 0.3) + (soft_hits * -1.5)
    return min(boost, MAX_KEYWORD_BOOST)
```

A `None` return is a hard filter — the post is dropped from ranking.

---

## 12. Saturation check

### v1.0 spec — title similarity against YouTube

For each candidate post that survives all hard filters AND has a pre-saturation composite score in the **top N** (`saturation.top_n_to_check`):

1. Clean post title (strip Reddit decorations like `[OC]`, `(OC)`, emojis).
2. Call YouTube Data API `search.list`:
   - `q` = cleaned title
   - `publishedAfter` = now - `saturation.lookback_days`
   - `order` = viewCount
   - `type` = video
   - `maxResults` = 5
3. Batch `videos.list` (statistics) and `channels.list` (subscriberCount) for all collected IDs across all candidates in this pass — one call each, up to 50 IDs per call.
4. For each result, compute `rapidfuzz.token_set_ratio(post_title, yt_title) / 100`. Filter to results above `title_similarity_threshold`.
5. Insert a `saturation_checks` row per post with the top match's stats.

### Decision rule

```python
if any_match.views > drop_if_views_above and any_match.channel_subs > drop_if_channel_subs_above:
    would_drop = True
else:
    would_drop = False

would_downrank_score = sum(match.views for match in matches) * downrank_per_1k_views / 1000
```

### Shadow mode

For the first 7 days (controlled by `saturation.mode`):
- All checks run and write to `saturation_checks` with `in_effect = false`.
- The ranker **ignores** `would_drop` and `would_downrank_score`.
- Operator weekly report compares "would have dropped" against consumer claims:
  - If saturation would have dropped clips the consumer claimed → calibration issue, raise thresholds.
  - If saturation correctly flagged clips the consumer dismissed for `too_saturated` → ramp into ranking.

When confidence is established, flip `saturation.mode` to `active` and existing `saturation_checks` rows get `in_effect = true` going forward.

### Quota budget

- `search.list` = 100 units. `videos.list` and `channels.list` = 1 unit each (batched).
- Per pass (top 30 candidates): 30 × 100 + 1 + 1 = **3,002 units**.
- Two passes/day (morning + evening digests) = 6,004 units.
- Free quota: 10,000 units/day. **Comfortable margin for v1.**
- Track usage in `system_state.youtube_quota_used_today`. Operator alert at 80%.

### Saturation roadmap (v1.1 → v3.0)

| Tier | What it adds | Trigger to build |
|---|---|---|
| **v1.1** | Search by canonicalized video URL + by top Reddit comment text | After 2 weeks of v1.0 data shows title-only is missing reuploads |
| **v1.2** | "Early bird" check: any match in last 24h regardless of view count, soft downrank only | After consumer reports a clip he beat by hours but lost on the YT algorithm |
| **v2.0** | Perceptual hashing of first 3 keyframes (yt-dlp → ffmpeg → pHash) against a rolling index of recent YouTube uploads | After v1.x evidence shows title-changed reuploads are slipping through |
| **v2.1** | TikTok title search via Apify or similar paid scraper | When TikTok-first virality becomes a measurable miss |
| **v3.0** | Audio fingerprinting (Chromaprint) | Only if v2 still has too many misses on re-edited clips |

Build incrementally. Do not jump to v2 without v1.x data justifying it.

---

## 13. Ranker

### Hard filters (applied before scoring)

A post is excluded if any of these is true:
- `age_hours < min_age_hours` OR `age_hours > max_age_hours`
- `latest.score < min_score`
- `domain not in allowed_domains`
- `nsfw and block_nsfw`
- `keyword_boost(title) is None` (HARD_BLACKLIST hit)
- `status in ('claimed', 'dismissed', 'expired')`
- `duration_s` is known AND outside `[min_seconds, max_seconds]`
- (saturation in `active` mode only) saturation `would_drop`

If `duration_s is None`, the post passes the duration filter but is flagged in the digest message ("⏱ duration unknown").

### Composite score

```python
score = (
    w.score_velocity_z   * z_score_velocity      +
    w.comment_velocity_z * z_comment_velocity    +
    w.acceleration_z     * z_acceleration        +
    w.keyword_boost      * keyword_boost(title)  +
    w.subreddit_weight   * subreddit.weight      -
    w.saturation_penalty * (saturation.would_downrank_score if saturation.in_effect else 0)
)
```

Component values, the final composite, and the active `weights_version` are stored alongside each digest entry so `/why` can reproduce the breakdown.

### Cold-start fallback

When baselines are immature (see §10.3), z-scores are replaced with raw values normalized by per-subreddit 7-day medians. The ranker still works; it just isn't statistically calibrated.

---

## 14. Telegram (consumer surface)

### Bot setup (one-time, document in README)

1. Talk to `@BotFather` → `/newbot` → save token.
2. Start the bot in a private chat → `/start` → log `update.effective_chat.id`. That's `TELEGRAM_CONSUMER_CHAT_ID`.
3. Repeat for the operator's chat → `TELEGRAM_OPERATOR_CHAT_ID`.

### Daily digest message format

One Telegram message per post in the top N. Format:

```
🔥 #1 — r/Karens · 3h old · score 847 (↑ 290/h)

Karen demands neighbor remove their dog from her sight

Why ranked: high comment velocity (z=2.4), strong keyword match (karen, dog),
trending in /rising. Saturation: clean (no big-channel matches).

[ ✅ Claim ]  [ ❌ Dismiss ]  [ 🔗 Open ]  [ ❓ Why ]
```

Inline keyboard `callback_data`:
- `claim:{post_id}` → set `status='claimed'`, write `post_actions` row, edit message to add ✅ Claimed footer.
- `dismiss:{post_id}` → replace inline keyboard with reason buttons (see below).
- `open:{post_id}` → write `post_actions` row, no state change. Open URL via the bot's hyperlink in the message text.
- `why:{post_id}` → ephemeral reply (deleted after 60s) with full velocity / z-score / keyword / saturation breakdown.

### Dismiss-reason follow-up

After dismiss tap, the message keyboard becomes:

```
[ Too saturated ] [ Too short ] [ Not Karen-y ]
[ Off-brand ] [ Boring ] [ Other ]
```

`callback_data` = `dismiss_reason:{post_id}:{reason_code}`. On tap: write the reason into the `post_actions` row created at dismiss, strikethrough the message body. **Do not block dismissal on reason selection** — if the consumer never taps a reason, leave `reason = NULL`. The reason is bonus signal, not required.

### Manual pickup (false-negative capture)

If the consumer pastes any Reddit URL into the chat (no command, plain message):
1. Bot detects URL pattern.
2. If post not in DB, ingest it immediately (one-shot PRAW fetch + `post_metrics` snapshot).
3. Write `post_actions` row with `action='manual_pickup'`.
4. Reply with confirmation: "Logged. We'll check why this didn't make the digest in the next operator review."
5. Operator weekly report dedicates a section to `manual_pickup` events: did our system see it? At what rank? Why was it filtered out?

### Commands

| Command | Behavior |
|---|---|
| `/start` | Onboarding message; logs the chat ID for setup |
| `/digest` | Re-send today's most recent digest |
| `/why <post_id>` | Same as the inline ❓ Why button — full score breakdown |
| `/stats` | Counts of claim / dismiss / ignore / manual_pickup over last 7 days |
| `/pause <hours>` | Suspend digests + alerts for N hours |
| `/resume` | Cancel a pause early |

Operator-only commands (only respond if `chat_id == TELEGRAM_OPERATOR_CHAT_ID`):
| Command | Behavior |
|---|---|
| `/inspect <post_id>` | Full post + metrics + saturation dump |
| `/quota` | Current YouTube API quota usage |
| `/health` | Heartbeat + last-success timestamps for each job |
| `/disable_sub <name>` | Manually disable a subreddit |
| `/enable_sub <name>` | Re-enable |

### Breaking alerts

A separate one-off message with a 🚨 prefix and the same buttons. Subject to:
- `breaking_alerts.daily_cap`
- `breaking_alerts.quiet_hours_*` — alerts during quiet hours are queued and surface in the morning digest as a "While you slept" prefix block.

### Status transitions persist

A claimed/dismissed post never reappears in any subsequent digest or alert.

### Acceptance criteria

- Digests arrive at 9:00 and 19:00 EST every day.
- Buttons fire callbacks within 1 second.
- Dismissed posts disappear from future digests.
- `/stats` returns sane numbers after a few days.
- A pasted Reddit URL is logged as a manual_pickup, even if the post wasn't previously in our DB.

---

## 15. Operator surface

### Operator daily digest (08:30 EST, before the consumer's)

Same format as the consumer digest but expanded:
- Top 30 candidates (vs. 10).
- For each: full score breakdown (every component value).
- Header summary: total posts ingested in last 24h, candidates after filters, claim/dismiss rates last 7 days, YouTube quota used, error count, list of any subreddits auto-disabled.
- Tail: any `manual_pickup` events from the consumer in the last 24h, with our rank for those posts (or "filtered out at: <reason>").
- "Cold-start fallback active for: r/X, r/Y" annotation if applicable.

### `scripts/analyze.py`

CLI tool wrapping a small set of pre-defined SQL views:
- `--hit-rate-by-sub` — claim count / digest-appearance count, last 30 days.
- `--dismiss-reasons` — distribution of reason codes, last 30 days.
- `--saturation-shadow-comparison` — for each shadow-mode flag, did the consumer also dismiss? Used to decide when to flip to active.
- `--keyword-correlations` — for each keyword, claim rate of posts that contained it.
- `--weight-sensitivity <weight_name> <new_value>` — re-run the ranker over the last 7 days of candidates with one weight changed; print the diff in top-10 composition.
- `--manual-pickup-misses` — every manual_pickup event with the system's rank for that post (or filter reason if dropped).

### `scripts/tune_weights.py`

Interactive: given a proposed `config.yaml` weight change, replays the last 14 days of digest candidates with the new weights and shows the swap-in/swap-out diff before you commit.

### `scripts/inspect_post.py <post_id>`

Dumps post row, full metric history, baseline comparison, saturation check history, keyword hits, every digest the post appeared in, and any `post_actions`.

---

## 16. Validation timeline (the phased rollout)

**Code is built in one push.** Validation is the part that's phased.

| Day | What happens | Consumer sees |
|---|---|---|
| 1–2 | Ingestion running, data accumulating, ranker disabled | Nothing |
| 3 | Operator runs `manual_digest.py`, eyeballs candidates | Nothing |
| 4 | Operator digest going to *us only* via Telegram, we tune cold-start weights | Nothing |
| 5 | First consumer digest. Conservative settings: breaking-alert `z_threshold=4.5`, saturation `mode=shadow`, digest size capped at 10 (no padding) | First digest arrives |
| 6–11 | Consumer uses normally. Daily operator digest reviews what's working. We tune `config.yaml` weights and keyword lists (NOT code) | Two digests/day + occasional alerts |
| 12–14 | Baselines have ≥7 days of data; z-scores calibrated. Saturation shadow comparison reviewed. | Two digests/day |
| 15+ | If saturation comparison checks out, flip `saturation.mode = active`. If alerts haven't been spamming, lower `z_threshold` to 3.5. | Two digests/day; saturation now downranks |

**Rule:** never ship a config change to the consumer if the operator-side `analyze.py` doesn't show it improving on historical data. Use `tune_weights.py` first.

---

## 17. Scheduler

APScheduler `AsyncIOScheduler` (we're integrating with the async Telegram bot).

| Job | Cadence (EST) | Description |
|---|---|---|
| `poll_subreddits` | every 10 min | Ingestion + metric repoll |
| `compute_baselines` | daily 03:00 | Rolling stats over last 14 days |
| `expire_old_posts` | daily 04:00 | Mark posts >7 days old as `expired` |
| `send_operator_digest` | daily 08:30 | Top 30 + diagnostics → operator |
| `send_morning_digest` | daily 09:00 | Top 10 → consumer |
| `send_evening_digest` | daily 19:00 | Top 10 → consumer (since this morning) |
| `breaking_alert_scan` | every 10 min (after poll) | Find posts crossing threshold; emit if cap allows |
| `heartbeat_check` | every 15 min | Verify `last_poll_ok_at` not stale + restart count; alert operator |
| `daily_smoke_check` | daily 11:00 | Zero-output / zero-error sanity check |

All jobs run inside the single Fly container as APScheduler tasks. No external cron, no external broker. The container has one process tree: `entrypoint.sh` → litestream (background) + python (main).

### Concurrency safety

`poll_subreddits` acquires a file lock at startup. If a previous run is still going, the new one no-ops with a warning.

---

## 18. Watchdog, monitoring & data durability

### 1. Process & liveness — Fly platform

Fly handles two things automatically:

- **Crash restart.** If the Python process exits non-zero, Fly restarts the machine. Configurable in `fly.toml` (`restart.policy = "on-failure"`).
- **Healthcheck restart.** Our app exposes a single `aiohttp` route at `GET /health`. It returns 200 if `system_state.last_poll_ok_at` is younger than `heartbeat_max_age_minutes`, else 503. Fly's platform health probe hits it every 30s; on 3 consecutive failures it restarts the machine.

The healthcheck logic catches "process is up but logically wedged" (PRAW hung, advisory lock stuck, scheduler dead) — cases a plain crash-restart misses.

### 2. Heartbeat to operator

We still want operator visibility when restarts happen, because repeated restarts mean a real bug, not transient noise.

In-process APScheduler job (`heartbeat_check`, every 15 min). Reads `system_state.last_poll_ok_at` and `system_state.restart_count_24h`. Pages operator via Telegram if:
- `last_poll_ok_at` is older than `heartbeat_max_age_minutes`, OR
- More than 3 restarts in the last 24h.

```
🚨 Karen-Finder: poller stale 32 min (max: 25). Last OK: 2026-04-28 14:03 EST.
Recent restarts (24h): 4. flyctl logs -a mihalis-db  to investigate.
```

Note: this runs *in* the main container, so a fully hung process can't fire it. That's fine — Fly's healthcheck already restarts a hung container; this layer exists to tell us *that* it happened.

### 3. Output-level — daily smoke check

`daily_smoke_check` job (11:00 EST). If yesterday produced **zero** claim-eligible candidates AND **zero** errors logged, that's suspicious — alert operator. Most likely: filters over-tightened, subreddits all disabled, or saturation accidentally in active mode with too-aggressive thresholds.

### 4. Data durability — Litestream → Cloudflare R2

The SQLite file lives on a Fly volume mounted at `/data`. **Fly volumes persist across deploys and restarts but are NOT replicated** — single-disk failure or accidental destroy = total loss. Litestream covers off-platform durability.

**How it works:**

- Litestream binary runs as a sidecar process inside the same container, started by `entrypoint.sh` *before* the Python app.
- `litestream replicate` watches the SQLite WAL file and streams changes to R2 in near-real-time (typical lag <1s).
- On every container start, `entrypoint.sh` first runs `litestream restore -if-replica-exists`. If `/data/karen_finder.db` doesn't exist on the volume but a replica exists in R2, it's pulled down. Idempotent: no-ops when local DB is current.

**`litestream.yml` (sketch):**
```yaml
dbs:
  - path: /data/karen_finder.db
    replicas:
      - type: s3
        endpoint: ${R2_ENDPOINT}
        bucket: ${R2_BUCKET}
        path: karen_finder
        access-key-id: ${R2_ACCESS_KEY_ID}
        secret-access-key: ${R2_SECRET_ACCESS_KEY}
        retention: 720h        # 30 days of point-in-time recovery
        snapshot-interval: 24h
```

**`entrypoint.sh` (sketch):**
```bash
#!/bin/sh
set -e
litestream restore -if-replica-exists -config /app/litestream.yml /data/karen_finder.db
litestream replicate -config /app/litestream.yml &
exec python -m karen_finder
```

**Recovery procedure (must be tested, see §22):**

```
fly machines run --image <image> --volume <new-volume>
# Container starts → entrypoint.sh runs `litestream restore` from R2 → app starts on restored DB.
```

Total recovery time: under 2 minutes for our DB size.

**Cost:** Cloudflare R2 free tier covers 10 GB storage + 10M Class A operations/month. We will use ~50 MB and a few thousand ops/day. **Effective cost: $0.**

### What is NOT a v1 watchdog concern

- Memory / CPU graphs (Fly dashboard has them).
- Disk space (Fly volume size is fixed; we monitor row counts via the operator digest, which is the real proxy).
- Log aggregation (`flyctl logs` is sufficient).
- Application-level replicas / multi-region. We're a single-machine workload.

---

## 19. Edge cases & gotchas

- **Deleted posts:** PRAW returns `[deleted]` author, `[removed]` selftext. Keep the row but exclude from digests (filter by `author != '[deleted]'`).
- **Author deleted account:** same handling.
- **Very fast posts:** a post posted 2 minutes ago with 5,000 upvotes will have absurd velocity. The `min_age_hours: 1` filter is the protection.
- **Reddit API outages:** log + retry next cycle. Don't crash. Don't backfill missed observations — gaps in the time-series are fine.
- **Subreddit goes private/banned:** catch the exception in the ingestion loop, set `subreddits.enabled = false`, post to operator Telegram.
- **Clock drift:** Fly machines run NTP'd clocks. No action needed beyond verifying with `date` after deploy. Velocity math is sensitive to bad clocks.
- **SQLite WAL mode:** enable on connection setup (`PRAGMA journal_mode=WAL`). Required by Litestream (replicates the WAL). Also improves concurrent-read performance for operator scripts running while the poller writes.
- **Fly volume is single-region, single-disk:** persistent across deploys/restarts but NOT replicated. Litestream is the durability layer (see §18.4).
- **Graceful shutdown on SIGTERM:** Fly sends SIGTERM before stopping a machine. The app must (a) stop the scheduler, (b) flush any in-flight DB writes, (c) give Litestream a moment to ship the final WAL frames before exit. Use `signal.signal(signal.SIGTERM, ...)` and a 10s grace window. Critical — abrupt termination can leave Litestream a few seconds behind on the replica.
- **Telegram message size limit:** 4,096 chars. The digest sends one message per post; not an issue. **Do not** combine into one mega-message later — buttons stop working.
- **Crosspost cycles:** depth limit 3 (see §9.4).
- **Duration unknown for non-Reddit-hosted videos:** pass through with a flag rather than filter out. Better to show a few too-long clips than silently drop gold.
- **Quiet hours alerts:** queue them, surface in morning digest. Don't drop silently.
- **YouTube quota exhaustion:** if the quota counter exceeds the daily limit, saturation check returns "skipped — quota exhausted" for the rest of the day. Posts pass through without saturation evaluation. Operator alerted.
- **Config version stamping:** every `post_action` row stores `config_version` (git short SHA at action time) and `weights_version` (hash of the relevant section of `config.yaml`). Lets us answer "did this dismiss happen under the old or new weights?" forever.
- **Title cleaning for saturation:** strip `[OC]`, `(OC)`, `[Karen]` prefixes, emojis, and trailing reddit-isms before YouTube search. Otherwise we miss reuploads where the YT title doesn't include those tags.

---

## 20. Testing

Unit tests for pure functions: `velocity.py`, `keywords.py`, `ranker.py`, `saturation.py` (mock the YouTube client). Use fixed inputs and frozen timestamps via `freezegun` or explicit `now` injection.

Smoke test: `scripts/manual_digest.py` is the integration test. Should run cleanly against a real DB.

**Do not** write integration tests against the real Reddit or YouTube APIs. Mock both.

---

## 21. Out of scope (v2+) — refuse scope creep

These have been considered and rejected for v1. Track in `BACKLOG.md`. Do not start without an explicit scope expansion decision.

- ❌ TikTok, Instagram, Twitter, Facebook ingestion
- ❌ Perceptual hashing of video frames (saturation v2.0)
- ❌ Audio fingerprinting (saturation v3.0)
- ❌ LLM classification of clip content
- ❌ Whisper transcription
- ❌ Auto-editing, auto-thumbnail, auto-publish
- ❌ Web dashboard / web UI
- ❌ Multi-creator / multi-consumer support
- ❌ Machine-learned ranking (rule-based on purpose; ML comes after we have labeled action data)
- ❌ Cross-platform consumer surface (no email, no SMS — Telegram only in v1; the formatter layer is abstracted so swapping is a 1-day change later)
- ❌ Auto-pruning of underperforming subreddits (flag in operator digest only)
- ❌ Backfill rejection list (showing him clips he's already seen builds confidence)
- ❌ Curated competitor-channel watchlist (saturation already uses sub count as proxy)
- ❌ Retroactive score recomputation when weights change

---

## 22. Done definition (v1)

v1 is done when, for **7 consecutive days**:
- The 9:00 and 19:00 EST digests arrive reliably.
- The consumer claims at least 1 clip from each digest.
- No more than 2 of the 10 digest items are "obviously wrong" (per consumer's judgment).
- Breaking alerts (when they fire) are claim-rate >40%.
- The system runs without manual intervention.
- Saturation has been flipped from `shadow` to `active` based on validated comparison data.

**Plus, before declaring v1 done — recovery drill (one-time):**
- Provision a fresh Fly machine + new volume.
- Confirm `entrypoint.sh` restores the SQLite DB from R2 and the app starts cleanly with the most recent action ledger and metric history intact.
- Untested backups are not backups. Do this *before* week 1 of consumer use, not after.

If those criteria hold, ship v1 and start the v2 backlog. If they don't, tune `config.yaml` weights, subreddit list, and keyword tables before adding any new code.
