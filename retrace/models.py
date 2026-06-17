"""SQLAlchemy ORM models for Retrace.

The FTS5 virtual table (``captures_fts``) and its sync triggers are not ORM-mapped;
they are created via raw DDL in :mod:`retrace.db`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC timestamp (stored without tz; all timestamps are UTC by convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Capture(Base):
    """One stored capture: extracted text + caption + thumbnail + metadata.

    The raw screen frame is never stored — only what is below persists.
    """

    __tablename__ = "captures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, index=True, default=utcnow)

    app_name: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    bundle_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    window_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    text: Mapped[str] = mapped_column(Text, default="")
    text_len: Mapped[int] = mapped_column(Integer, default=0)
    # 'accessibility' | 'ocr' | 'mixed' | 'none'
    text_source: Mapped[str] = mapped_column(String, default="none")

    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption_model: Mapped[str | None] = mapped_column(String, nullable=True)

    # Hash of text+app+window for dedup. Not a secret.
    content_hash: Mapped[str | None] = mapped_column(String, index=True, nullable=True)

    thumb_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    calendar_event: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    embedding: Mapped["CaptureEmbedding | None"] = relationship(
        back_populates="capture",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_captures_app_time", "app_name", "captured_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Capture id={self.id} app={self.app_name!r} at={self.captured_at}>"


class CaptureHtml(Base):
    """Raw page HTML for a capture, gzip-compressed. Stored but not shown in the UI.

    Kept in a side table so the high-volume ``captures`` rows + FTS stay lean.
    """

    __tablename__ = "capture_html"

    capture_id: Mapped[int] = mapped_column(
        ForeignKey("captures.id", ondelete="CASCADE"), primary_key=True
    )
    length: Mapped[int] = mapped_column(Integer)  # uncompressed character length
    html_gz: Mapped[bytes] = mapped_column(LargeBinary)


class CaptureEmbedding(Base):
    """A single NL contextual embedding vector for a capture (float32, packed BLOB)."""

    __tablename__ = "capture_embeddings"

    capture_id: Mapped[int] = mapped_column(
        ForeignKey("captures.id", ondelete="CASCADE"), primary_key=True
    )
    dim: Mapped[int] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    vec: Mapped[bytes] = mapped_column(LargeBinary)

    capture: Mapped[Capture] = relationship(back_populates="embedding")


class ActivityEvent(Base):
    """A time-analytics interval. Complements the capture timeline."""

    __tablename__ = "activity_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 'knowledgec' | 'safari' | 'chrome' | 'active'
    source: Mapped[str] = mapped_column(String, index=True)
    app: Mapped[str] = mapped_column(String, index=True)  # bundle id / identifier
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)

    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    day: Mapped[str] = mapped_column(String, index=True)  # YYYY-MM-DD (local)

    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("source", "app", "url", "start_at", name="uq_activity_identity"),
        Index("ix_activity_day_source", "day", "source"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<ActivityEvent {self.source} {self.app} {self.seconds:.0f}s>"
