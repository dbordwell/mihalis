"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-28
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "posts",
        sa.Column("post_id", sa.String(), primary_key=True),
        sa.Column("subreddit", sa.String(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("author", sa.String()),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String()),
        sa.Column("created_utc", sa.Integer(), nullable=False),
        sa.Column("is_video", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_self", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("nsfw", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("duration_s", sa.Integer()),
        sa.Column("crosspost_parent", sa.String()),
        sa.Column("also_seen_in", sa.Text()),
        sa.Column("status", sa.String(), nullable=False, server_default="new"),
        sa.Column("first_seen_at", sa.Integer(), nullable=False),
        sa.Column("last_updated_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_posts_subreddit_created", "posts", ["subreddit", "created_utc"])
    op.create_index("ix_posts_status_created", "posts", ["status", "created_utc"])
    op.create_index("ix_posts_url", "posts", ["url"])

    op.create_table(
        "post_metrics",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("post_id", sa.String(), sa.ForeignKey("posts.post_id"), nullable=False),
        sa.Column("observed_at", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("num_comments", sa.Integer(), nullable=False),
        sa.Column("upvote_ratio", sa.Float()),
        sa.Column("in_rising", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("post_id", "observed_at", name="uq_post_metrics_post_observed"),
    )
    op.create_index("ix_post_metrics_post_observed_desc", "post_metrics", ["post_id", "observed_at"])

    op.create_table(
        "subreddit_baselines",
        sa.Column("subreddit", sa.String(), nullable=False),
        sa.Column("age_bucket", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(), nullable=False),
        sa.Column("mean", sa.Float(), nullable=False),
        sa.Column("stdev", sa.Float(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("subreddit", "age_bucket", "metric"),
    )

    op.create_table(
        "subreddits",
        sa.Column("name", sa.String(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("disabled_reason", sa.Text()),
        sa.Column("last_polled_at", sa.Integer()),
    )

    op.create_table(
        "post_actions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("post_id", sa.String(), sa.ForeignKey("posts.post_id"), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("reason", sa.String()),
        sa.Column("config_version", sa.String(), nullable=False),
        sa.Column("weights_version", sa.String(), nullable=False),
        sa.Column("acted_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_post_actions_post_acted", "post_actions", ["post_id", "acted_at"])
    op.create_index("ix_post_actions_action_acted", "post_actions", ["action", "acted_at"])

    op.create_table(
        "saturation_checks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("post_id", sa.String(), sa.ForeignKey("posts.post_id"), nullable=False),
        sa.Column("checked_at", sa.Integer(), nullable=False),
        sa.Column("matches_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top_match_views", sa.Integer()),
        sa.Column("top_match_channel_subs", sa.Integer()),
        sa.Column("top_match_url", sa.Text()),
        sa.Column("top_match_title_similarity", sa.Float()),
        sa.Column("would_drop", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("would_downrank_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("in_effect", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_saturation_post_checked", "saturation_checks", ["post_id", "checked_at"])

    op.create_table(
        "digest_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("digest_type", sa.String(), nullable=False),
        sa.Column("sent_at", sa.Integer(), nullable=False),
        sa.Column("post_ids", sa.Text(), nullable=False),
        sa.Column("recipient", sa.String(), nullable=False),
        sa.Column("config_version", sa.String(), nullable=False),
        sa.Column("weights_version", sa.String(), nullable=False),
    )

    op.create_table(
        "system_state",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("system_state")
    op.drop_table("digest_history")
    op.drop_index("ix_saturation_post_checked", table_name="saturation_checks")
    op.drop_table("saturation_checks")
    op.drop_index("ix_post_actions_action_acted", table_name="post_actions")
    op.drop_index("ix_post_actions_post_acted", table_name="post_actions")
    op.drop_table("post_actions")
    op.drop_table("subreddits")
    op.drop_table("subreddit_baselines")
    op.drop_index("ix_post_metrics_post_observed_desc", table_name="post_metrics")
    op.drop_table("post_metrics")
    op.drop_index("ix_posts_url", table_name="posts")
    op.drop_index("ix_posts_status_created", table_name="posts")
    op.drop_index("ix_posts_subreddit_created", table_name="posts")
    op.drop_table("posts")
