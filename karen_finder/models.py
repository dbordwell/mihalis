"""ORM models. Mirror PLAN.md §6 verbatim."""

from __future__ import annotations

from sqlalchemy import (
    Boolean, Float, ForeignKey, Index, Integer, PrimaryKeyConstraint, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Post(Base):
    __tablename__ = "posts"

    post_id: Mapped[str] = mapped_column(String, primary_key=True)
    subreddit: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(String)
    created_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    is_video: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_self: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    nsfw: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    crosspost_parent: Mapped[str | None] = mapped_column(String)
    also_seen_in: Mapped[str | None] = mapped_column(Text)  # JSON array
    status: Mapped[str] = mapped_column(String, nullable=False, default="new")
    first_seen_at: Mapped[int] = mapped_column(Integer, nullable=False)
    last_updated_at: Mapped[int] = mapped_column(Integer, nullable=False)

    metrics: Mapped[list["PostMetric"]] = relationship(back_populates="post", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_posts_subreddit_created", "subreddit", "created_utc"),
        Index("ix_posts_status_created", "status", "created_utc"),
        Index("ix_posts_url", "url"),
    )


class PostMetric(Base):
    __tablename__ = "post_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[str] = mapped_column(String, ForeignKey("posts.post_id"), nullable=False)
    observed_at: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    num_comments: Mapped[int] = mapped_column(Integer, nullable=False)
    upvote_ratio: Mapped[float | None] = mapped_column(Float)
    in_rising: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    post: Mapped[Post] = relationship(back_populates="metrics")

    __table_args__ = (
        UniqueConstraint("post_id", "observed_at", name="uq_post_metrics_post_observed"),
        Index("ix_post_metrics_post_observed_desc", "post_id", "observed_at"),
    )


class SubredditBaseline(Base):
    __tablename__ = "subreddit_baselines"

    subreddit: Mapped[str] = mapped_column(String, nullable=False)
    age_bucket: Mapped[int] = mapped_column(Integer, nullable=False)
    metric: Mapped[str] = mapped_column(String, nullable=False)
    mean: Mapped[float] = mapped_column(Float, nullable=False)
    stdev: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (PrimaryKeyConstraint("subreddit", "age_bucket", "metric"),)


class SubredditRegistry(Base):
    __tablename__ = "subreddits"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    last_polled_at: Mapped[int | None] = mapped_column(Integer)


class PostAction(Base):
    __tablename__ = "post_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[str] = mapped_column(String, ForeignKey("posts.post_id"), nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(String)
    config_version: Mapped[str] = mapped_column(String, nullable=False)
    weights_version: Mapped[str] = mapped_column(String, nullable=False)
    acted_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_post_actions_post_acted", "post_id", "acted_at"),
        Index("ix_post_actions_action_acted", "action", "acted_at"),
    )


class SaturationCheck(Base):
    __tablename__ = "saturation_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[str] = mapped_column(String, ForeignKey("posts.post_id"), nullable=False)
    checked_at: Mapped[int] = mapped_column(Integer, nullable=False)
    matches_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    top_match_views: Mapped[int | None] = mapped_column(Integer)
    top_match_channel_subs: Mapped[int | None] = mapped_column(Integer)
    top_match_url: Mapped[str | None] = mapped_column(Text)
    top_match_title_similarity: Mapped[float | None] = mapped_column(Float)
    would_drop: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    would_downrank_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    in_effect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_saturation_post_checked", "post_id", "checked_at"),)


class DigestHistory(Base):
    __tablename__ = "digest_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    digest_type: Mapped[str] = mapped_column(String, nullable=False)
    sent_at: Mapped[int] = mapped_column(Integer, nullable=False)
    post_ids: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list
    recipient: Mapped[str] = mapped_column(String, nullable=False)
    config_version: Mapped[str] = mapped_column(String, nullable=False)
    weights_version: Mapped[str] = mapped_column(String, nullable=False)


class SystemState(Base):
    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)
