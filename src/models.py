import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    google_sub: Mapped[str] = mapped_column(String, unique=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    max_stored_matches: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    matches: Mapped[list["Match"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    player_notes: Mapped[list["PlayerNote"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    map_name: Mapped[str] = mapped_column(String)
    duration: Mapped[str] = mapped_column(String)
    mode: Mapped[str] = mapped_column(String)
    queue_type: Mapped[str] = mapped_column(String)
    result: Mapped[str] = mapped_column(String)
    played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    is_backfill: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    source: Mapped[str] = mapped_column(String, default="", server_default="")
    scoreboard_url: Mapped[str | None] = mapped_column(String, nullable=True)
    hero_stats_url: Mapped[str | None] = mapped_column(String, nullable=True)
    rank_min: Mapped[str | None] = mapped_column(String, nullable=True)
    rank_max: Mapped[str | None] = mapped_column(String, nullable=True)
    is_wide_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    banned_heroes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    initial_team_side: Mapped[str | None] = mapped_column(String, nullable=True)
    score_progression: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    player_stats: Mapped[list["PlayerStat"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )
    screenshots: Mapped[list["Screenshot"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )
    files: Mapped[list["MatchFile"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )
    rank_update: Mapped["RankUpdate | None"] = relationship(
        back_populates="match", cascade="all, delete-orphan", uselist=False
    )
    user: Mapped["User"] = relationship(back_populates="matches")


class PlayerStat(Base):
    __tablename__ = "player_stats"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("matches.id", ondelete="CASCADE")
    )
    team: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    player_name: Mapped[str] = mapped_column(String)
    eliminations: Mapped[int | None] = mapped_column(Integer)
    assists: Mapped[int | None] = mapped_column(Integer)
    deaths: Mapped[int | None] = mapped_column(Integer)
    damage: Mapped[int | None] = mapped_column(Integer)
    healing: Mapped[int | None] = mapped_column(Integer)
    mitigation: Mapped[int | None] = mapped_column(Integer)
    is_self: Mapped[bool] = mapped_column(Boolean, default=False)
    in_party: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    joined_at: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    hero: Mapped[str | None] = mapped_column(String, nullable=True)
    swap_snapshots: Mapped[list | None] = mapped_column(JSON, nullable=True)

    match: Mapped["Match"] = relationship(back_populates="player_stats")
    hero_stats: Mapped[list["HeroStat"]] = relationship(
        back_populates="player_stat", cascade="all, delete-orphan"
    )


class HeroStat(Base):
    __tablename__ = "hero_stats"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    player_stat_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("player_stats.id", ondelete="CASCADE")
    )
    hero_name: Mapped[str] = mapped_column(String)
    started_at: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)

    player_stat: Mapped["PlayerStat"] = relationship(back_populates="hero_stats")
    values: Mapped[list["HeroStatValue"]] = relationship(
        back_populates="hero_stat", cascade="all, delete-orphan"
    )


class HeroStatValue(Base):
    __tablename__ = "hero_stat_values"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    hero_stat_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("hero_stats.id", ondelete="CASCADE")
    )
    label: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(String)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)

    hero_stat: Mapped["HeroStat"] = relationship(back_populates="values")


class RankUpdate(Base):
    __tablename__ = "rank_updates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("matches.id", ondelete="CASCADE"), unique=True
    )
    rank: Mapped[str] = mapped_column(String)
    division: Mapped[int] = mapped_column(Integer)
    progress_pct: Mapped[int] = mapped_column(Integer)
    delta_pct: Mapped[int] = mapped_column(Integer)
    demotion_protection: Mapped[bool] = mapped_column(Boolean, default=False)
    modifiers: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    match: Mapped["Match"] = relationship(back_populates="rank_update")


class PlayerNote(Base):
    __tablename__ = "player_notes"
    __table_args__ = (
        UniqueConstraint("user_id", "player_name", name="uq_player_notes_user_player"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    player_name: Mapped[str] = mapped_column(String)
    note: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    user: Mapped["User"] = relationship(back_populates="player_notes")


class Screenshot(Base):
    __tablename__ = "screenshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("matches.id", ondelete="CASCADE")
    )
    url: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    match: Mapped["Match"] = relationship(back_populates="screenshots")


class MatchFile(Base):
    __tablename__ = "match_files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("matches.id", ondelete="CASCADE")
    )
    filename: Mapped[str] = mapped_column(String)
    size: Mapped[int] = mapped_column(BigInteger)
    tus_id: Mapped[str] = mapped_column(String, unique=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    match: Mapped["Match"] = relationship(back_populates="files")
