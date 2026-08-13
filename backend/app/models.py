"""Модели данных.

Универсальная таблица metrics — один формат для всех коллекторов и графиков:
(project_id, metric, ts, value, meta). Для рыночных метрик (BTC trends,
ранг Coinbase) project_id = "_market".
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MARKET = "_market"  # sentinel project_id для рыночных метрик


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # slug, напр. "solana"
    name: Mapped[str] = mapped_column(String)
    symbol: Mapped[str] = mapped_column(String)
    chain: Mapped[str] = mapped_column(String, default="")  # своя сеть или сеть токена
    coingecko_id: Mapped[str] = mapped_column(String, default="")
    binance_symbol: Mapped[str] = mapped_column(String, default="")  # напр. SOLUSDT
    github_org: Mapped[str] = mapped_column(String, default="")
    github_repos: Mapped[str] = mapped_column(Text, default="")  # JSON-список "org/repo"
    twitter_project: Mapped[str] = mapped_column(String, default="")
    twitter_ceo: Mapped[str] = mapped_column(String, default="")
    discord_export_dir: Mapped[str] = mapped_column(String, default="")  # папка с JSON DiscordChatExporter
    token_contracts: Mapped[str] = mapped_column(Text, default="")  # JSON {chain: contract} для он-чейн эвристик
    trends_keyword: Mapped[str] = mapped_column(String, default="")  # запрос для Google Trends
    genesis_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    wallets: Mapped[list[Wallet]] = relationship(back_populates="project")


class Metric(Base):
    __tablename__ = "metrics"
    __table_args__ = (
        UniqueConstraint("project_id", "metric", "ts", name="uq_metric_point"),
        Index("ix_metrics_lookup", "project_id", "metric", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String, default=MARKET)
    metric: Mapped[str] = mapped_column(String)
    ts: Mapped[datetime] = mapped_column(DateTime)
    value: Mapped[float] = mapped_column(Float)
    meta: Mapped[str] = mapped_column(Text, default="")  # JSON при необходимости


class Event(Base):
    """Точечные события: разлоки, раунды фандинга, подключение валидаторов."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("project_id", "type", "ts", "title", name="uq_event"),
        Index("ix_events_lookup", "project_id", "type", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)  # unlock | funding | validator | custom
    ts: Mapped[datetime] = mapped_column(DateTime)
    title: Mapped[str] = mapped_column(String, default="")
    value: Mapped[float] = mapped_column(Float, default=0.0)  # $ раунда, токены разлока...
    meta: Mapped[str] = mapped_column(Text, default="")


class Wallet(Base):
    """Помеченные кошельки команды/фонда/инвесторов."""

    __tablename__ = "wallets"
    __table_args__ = (UniqueConstraint("chain", "address", name="uq_wallet"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    chain: Mapped[str] = mapped_column(String)  # ethereum | bsc | arbitrum | ...
    address: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String, default="team")  # team | foundation | investor
    name: Mapped[str] = mapped_column(String, default="")

    project: Mapped[Project] = relationship(back_populates="wallets")


class WalletFlow(Base):
    """Движения по помеченным кошелькам (переводы токена проекта)."""

    __tablename__ = "wallet_flows"
    __table_args__ = (
        UniqueConstraint("tx_hash", "wallet_id", "direction", name="uq_flow"),
        Index("ix_flows_lookup", "wallet_id", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"))
    ts: Mapped[datetime] = mapped_column(DateTime)
    direction: Mapped[str] = mapped_column(String)  # in | out
    amount: Mapped[float] = mapped_column(Float)  # в токенах
    token: Mapped[str] = mapped_column(String, default="")
    tx_hash: Mapped[str] = mapped_column(String)
    counterparty: Mapped[str] = mapped_column(String, default="")
    to_exchange: Mapped[bool] = mapped_column(Boolean, default=False)  # out на биржу = вероятная продажа


class Mention(Base):
    """Сырые упоминания в СМИ (GDELT, CryptoPanic) — фильтруем при агрегации."""

    __tablename__ = "mentions"
    __table_args__ = (
        UniqueConstraint("project_id", "url", name="uq_mention"),
        Index("ix_mentions_lookup", "project_id", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)  # gdelt | cryptopanic
    ts: Mapped[datetime] = mapped_column(DateTime)
    url: Mapped[str] = mapped_column(String)
    domain: Mapped[str] = mapped_column(String, default="")
    title: Mapped[str] = mapped_column(Text, default="")
    is_pr: Mapped[bool] = mapped_column(Boolean, default=False)  # PR-wire / проплаченная площадка


class ScreenerCandidate(Base):
    """Снапшоты кандидатов скринера (ф.3) — ручное утверждение в projects.yaml."""

    __tablename__ = "screener_candidates"
    __table_args__ = (
        UniqueConstraint("snapshot_date", "coingecko_id", name="uq_candidate"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_date: Mapped[datetime] = mapped_column(DateTime)
    coingecko_id: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    symbol: Mapped[str] = mapped_column(String)
    market_cap: Mapped[float] = mapped_column(Float, default=0.0)
    fdv: Mapped[float] = mapped_column(Float, default=0.0)
    volume_24h: Mapped[float] = mapped_column(Float, default=0.0)
    categories: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")  # почему прошёл фильтры
