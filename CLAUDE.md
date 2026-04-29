# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read this first

Project: **Karen-Finder** — a personal-use Reddit discovery pipeline that surfaces trending video posts to a single end-user via Telegram. Full spec lives in `PLAN.md`. **Read it before writing any code.** This file is orientation; `PLAN.md` is the source of truth.

## Two-user model — internalize this

The system has two distinct users with non-overlapping needs:

- **Consumer** — non-technical end-user. Interacts only via Telegram. Receives digests, taps claim/dismiss, occasionally forwards Reddit URLs.
- **Operator** — developer maintaining the pipeline. Hosts on Fly.io, watches data, tunes weights, debugs. Needs observability the consumer doesn't.

When designing any feature, ask: "is this consumer-facing or operator-facing?" The two surfaces have different priorities — consumer is **polished, sparse, conservative**; operator is **noisy, rich, diagnostic**. Don't bleed operator concerns into the consumer surface.

## Build philosophy

**Write code in one push; phase the validation, not the build.** The original phased plan was overly conservative. Build the whole v1 — schema, ingestion, ranking, saturation v1.0, Telegram, operator digest, watchdog — then deploy and let data accumulate. The validation gates are about *human eyeballs on real data*, not about code-write order.

Validation timeline (`PLAN.md` §16, summarized):
- Days 1–2: data accumulating, no Telegram to consumer
- Days 3–4: operator digest only; we tune cold-start weights
- Day 5+: consumer gets first digest, conservative thresholds, saturation in shadow mode
- Week 2: tune weights from claim/dismiss + reasons
- Week 3: saturation goes live (was shadow before)

**Do not ship Telegram delivery to the consumer before day 5.** Trust loss from a bad week-1 experience kills the project. This is the highest-stakes rule in the project.

## Hard scope guardrails

The plan deliberately excludes things that look helpful but are out of scope. **Refuse to add them without an explicit decision to expand v1:**

- ❌ TikTok, Instagram, Twitter, Facebook
- ❌ LLM-based classification of content
- ❌ Whisper / audio transcription
- ❌ Auto-editing, auto-thumbnail, auto-publish
- ❌ Web dashboard / web UI of any kind
- ❌ Email or SMS delivery (Telegram only in v1; formatter layer is abstracted for later swap)
- ❌ Multi-creator support
- ❌ Machine-learned ranking
- ❌ Perceptual hashing of video frames (saturation v2.0)
- ❌ Audio fingerprinting (saturation v3.0)
- ❌ Auto-pruning of underperforming subreddits (flag in operator digest only)
- ❌ Backfill rejection list (showing consumer clips he's seen builds confidence)
- ❌ Retroactive score recomputation when weights change

## Stack constraints — do not introduce

- ❌ Celery, Redis, Kafka, RabbitMQ
- ❌ FastAPI, Flask (no web surface in v1)
- ❌ Postgres (SQLite is the v1 choice — see `PLAN.md` §3 for why)
- ❌ Docker Compose, Kubernetes, Helm
- ❌ Any frontend framework

If you think you need any of these, you're out of scope.

**One narrow exception:** a single `aiohttp` route at `GET /health` is allowed for Fly's platform liveness probe. It returns 200 if `last_poll_ok_at` is recent, 503 otherwise. Do not add additional routes. This is a health endpoint, not a web app.

## Stack at a glance

Python 3.11+ · PRAW · SQLite + SQLAlchemy 2.x + Alembic · APScheduler · python-telegram-bot v20+ · structlog · pydantic-settings · YAML config · rapidfuzz · google-api-python-client · aiohttp (healthcheck only) · **Docker on Fly.io** · **Litestream → Cloudflare R2** for SQLite durability · Time zone: `America/New_York`

## Operating context

- Consumer time zone: `America/New_York` (EST/EDT). Schedule digests in his TZ via APScheduler, not UTC.
- Hosting: Fly.io single machine, persistent volume mounted at `/data`, deployed from GitHub.
- Secrets: `flyctl secrets set` in production; `.env` is local-dev only.
- Telegram only for delivery — both consumer and operator.
- Saturation runs in `shadow` mode for week 1 (logs decisions, zero ranking impact). Flip to `active` only after operator validates the shadow log against consumer claim/dismiss.
- Watchdog has three layers: **process+liveness** (Fly platform restart on crash or `/health` failure), **heartbeat** (in-process APScheduler job pages operator on stale poll or excess restarts), **output** (daily smoke check). All required for v1 done.
- **Data durability:** SQLite file lives on `/data` (Fly volume — persistent but not replicated). Litestream sidecar streams the WAL to Cloudflare R2 in near real-time. Recovery is `litestream restore` in `entrypoint.sh` on container start. **Recovery must be drilled on a fresh machine before v1 is declared done** — untested backups are not backups.
- YouTube Data API daily free quota is 10,000 units. Saturation check uses ~6,000 of those at planned cadence. Operator alerted at 80% usage.

## Versioning rule

Every `post_action` row stamps `config_version` (git short SHA) and `weights_version` (hash of relevant `config.yaml` section). When weights change, we never lose the ability to ask "would the old config have ranked this differently?" Tuning iterates on weights *frequently*; tracking versions costs nothing and is the difference between confident tuning and flying blind.

## Done definition

For **7 consecutive days**: digests arrive on schedule, consumer claims ≥1 clip per digest, ≤2/10 are "obviously wrong" per his judgment, breaking alerts have >40% claim rate, system runs without manual intervention, saturation has been flipped from shadow to active. See `PLAN.md` §22.
