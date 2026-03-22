import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    player_stats: Mapped[list["PlayerStat"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )
    screenshots: Mapped[list["Screenshot"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )


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


class PlayerNote(Base):
    __tablename__ = "player_notes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    player_name: Mapped[str] = mapped_column(String, unique=True)
    note: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
