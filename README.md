# Karen-Finder

Personal-use Reddit discovery pipeline that surfaces trending video posts to a single end-user via Telegram. See `PLAN.md` for the full v1 spec.

This README is for the **operator** (the developer maintaining the service). The end-user never sees this; their interface is Telegram only.

## What this is

A read-only Python service that polls a curated set of public subreddits at low volume (~22 requests every 10 minutes across 9 subreddits), ranks recent video posts by velocity + keyword match + saturation against existing YouTube uploads, and delivers two daily Telegram digests + occasional breaking alerts to a single end-user. No content is automatically republished; the end-user manually reviews each clip.

## Stack

Python 3.11+ · PRAW · SQLite (WAL) + SQLAlchemy + Alembic · APScheduler · python-telegram-bot · aiohttp (single `/health` route only) · Litestream → Cloudflare R2 · Docker on Fly.io.

## One-time setup

### 1. Reddit credentials

1. Sign in to reddit.com → preferences → apps → "create another app".
2. Type: **script**. Redirect URI: `http://localhost:8080`.
3. Copy the client ID (under the app name) and the secret.
4. Set them via `flyctl secrets set` (production) or in `.env` (local).

### 2. YouTube Data API key

1. Go to console.cloud.google.com → enable YouTube Data API v3 → create an API key.
2. Restrict it to YouTube Data API v3.
3. Set `YOUTUBE_API_KEY` via `flyctl secrets set`.

### 3. Telegram bot

1. Talk to `@BotFather` → `/newbot` → save the token.
2. Start a private chat with your bot → `/start`. The bot logs `effective_chat.id`; that's `TELEGRAM_CONSUMER_CHAT_ID`. Repeat for the operator's chat.
3. Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CONSUMER_CHAT_ID`, `TELEGRAM_OPERATOR_CHAT_ID`.

### 4. Cloudflare R2 (production durability)

1. Create an R2 bucket named `karen-finder-backup` (or anything; update `R2_BUCKET`).
2. Create an R2 API token (Object Read & Write) and copy the access key ID + secret.
3. The R2 endpoint format is `https://<account-id>.r2.cloudflarestorage.com`.
4. Set the four `R2_*` vars via `flyctl secrets set`.

### 5. Fly.io app

```bash
flyctl auth login
flyctl apps create mihalis-db         # already done
flyctl volumes create karen_data --size 1 --region iad
flyctl deploy
```

The first deploy provisions the volume mount, runs `entrypoint.sh`, restores from R2 (no-op if empty), runs `alembic upgrade head`, then starts the service.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env       # fill in Reddit creds at minimum
alembic upgrade head
python -m karen_finder poll-once   # one polling pass, exits
python -m karen_finder serve       # long-lived service
```

## Useful commands

```bash
# tail prod logs
flyctl logs -a mihalis-db

# ssh into the running container
flyctl ssh console -a mihalis-db

# inspect a specific post (after data has been collected)
python scripts/inspect_post.py t3_abc123

# health endpoint
curl https://mihalis-db.fly.dev/health
```

## Recovery drill (must be performed before v1 done)

```bash
flyctl volumes destroy <volume-id>             # ⚠ destructive — only on a non-prod volume
flyctl volumes create karen_data --size 1 --region iad
flyctl deploy
# Verify entrypoint.sh logs show "litestream restore ... ok" and the latest action ledger is intact.
```

## Project layout

See `PLAN.md` §5.

## Contributing

This is a single-creator side project. There is no contribution flow. If you're picking it up after the fact: read `CLAUDE.md`, then `PLAN.md`, in that order.
