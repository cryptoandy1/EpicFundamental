"""REST API для дашбордов Next.js.

  GET /api/market/overview   — «Обзор рынка»: BTC trends + перцентиль-алерт, ранг Coinbase, цена BTC
  GET /api/projects          — пул: сводка по каждому проекту
  GET /api/projects/{id}     — страница проекта: все графики одним бандлом
  GET /api/screener          — кандидаты последнего прогона скринера
  GET /api/ladder            — композитный скор «лесенки»
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import load_config, sync_projects
from .db import SessionLocal, init_db
from .models import MARKET, Event, Metric, Mention, Project, ScreenerCandidate, Wallet, WalletFlow
from .scoring import compute_ladder

app = FastAPI(title="EpicFundamental API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    session = SessionLocal()
    try:
        sync_projects(session)
    finally:
        session.close()


def _series(session: Session, project_id: str, metric: str) -> list[list]:
    rows = (
        session.query(Metric.ts, Metric.value)
        .filter(Metric.project_id == project_id, Metric.metric == metric)
        .order_by(Metric.ts)
        .all()
    )
    return [[ts.isoformat(), value] for ts, value in rows]


def _peers(session: Session, project_id: str, metric: str, points: int = 104) -> dict[str, list[list]]:
    """Ряды остальных монет пула по метрике (для графиков «vs аналоги»), хвост points точек."""
    peers: dict[str, list[list]] = {}
    for other in session.query(Project).filter(Project.approved, Project.id != project_id).all():
        s = _series(session, other.id, metric)
        if s:
            peers[other.symbol] = s[-points:]
    return peers


def _events(session: Session, project_id: str, type_: str) -> list[dict]:
    rows = (
        session.query(Event)
        .filter(Event.project_id == project_id, Event.type == type_)
        .order_by(Event.ts)
        .all()
    )
    return [
        {
            "ts": e.ts.isoformat(),
            "title": e.title,
            "value": e.value,
            "meta": json.loads(e.meta) if e.meta else None,
        }
        for e in rows
    ]


def _percentile_of_latest(series: list[list]) -> float | None:
    values = [v for _, v in series if v is not None]
    if len(values) < 10:
        return None
    latest = values[-1]
    return round(sum(1 for v in values if v < latest) / len(values) * 100, 1)


@app.get("/api/market/overview")
def market_overview():
    session = SessionLocal()
    try:
        trends_weekly = _series(session, MARKET, "trends_weekly")
        trends_monthly = _series(session, MARKET, "trends_monthly")
        pct = _percentile_of_latest(trends_weekly)
        return {
            "btc_price": _series(session, MARKET, "btc_price_usd"),
            "btc_trends_monthly": trends_monthly,
            "btc_trends_weekly": trends_weekly,
            # перцентиль текущего интереса за 5 лет: > 90 — зона «пик = сливаем»
            "trends_percentile": pct,
            "sell_signal": pct is not None and pct >= 90,
            "coinbase_rank_overall": _series(session, MARKET, "coinbase_rank_overall"),
            "coinbase_rank_finance": _series(session, MARKET, "coinbase_rank_finance"),
        }
    finally:
        session.close()


@app.get("/api/projects")
def list_projects():
    session = SessionLocal()
    try:
        ladder = {r["project"]: r["score"] for r in compute_ladder(session)}
        out = []
        for p in session.query(Project).filter_by(approved=True).all():
            price = (
                session.query(Metric.value)
                .filter(Metric.project_id == p.id, Metric.metric == "price_usd")
                .order_by(Metric.ts.desc())
                .first()
            )
            cap = (
                session.query(Metric.value)
                .filter(Metric.project_id == p.id, Metric.metric == "market_cap")
                .order_by(Metric.ts.desc())
                .first()
            )
            out.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "symbol": p.symbol,
                    "chain": p.chain,
                    "genesis_date": p.genesis_date.isoformat() if p.genesis_date else None,
                    "price_usd": price[0] if price else None,
                    "market_cap": cap[0] if cap else None,
                    "score": ladder.get(p.id),
                }
            )
        return out
    finally:
        session.close()


@app.get("/api/projects/{project_id}")
def project_detail(project_id: str, exclude_pr: bool = True):
    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, f"проект {project_id} не найден")

        # возраст (ф.4): если CoinGecko не отдал genesis — считаем от первой свечи
        genesis = project.genesis_date
        if genesis is None:
            first_price = (
                session.query(Metric.ts)
                .filter(Metric.project_id == project_id, Metric.metric == "price_usd")
                .order_by(Metric.ts)
                .first()
            )
            genesis = first_price[0] if first_price else None

        # потоки кошельков команды (ф.7): недельные агрегаты + крупнейшие выводы
        top_outflows = (
            session.query(WalletFlow, Wallet)
            .join(Wallet, WalletFlow.wallet_id == Wallet.id)
            .filter(Wallet.project_id == project_id, WalletFlow.direction == "out")
            .order_by(WalletFlow.amount.desc())
            .limit(20)
            .all()
        )

        # упоминания (ф.10): лента свежих статей
        mentions_query = session.query(Mention).filter(Mention.project_id == project_id)
        if exclude_pr:
            mentions_query = mentions_query.filter(Mention.is_pr.is_(False))
        recent_mentions = mentions_query.order_by(Mention.ts.desc()).limit(30).all()

        curation = (load_config().get("twitter_curation") or {}).get(project_id, [])

        return {
            "project": {
                "id": project.id,
                "name": project.name,
                "symbol": project.symbol,
                "chain": project.chain,
                "genesis_date": genesis.isoformat() if genesis else None,
                "twitter_project": project.twitter_project,
                "twitter_ceo": project.twitter_ceo,
                "github_repos": json.loads(project.github_repos or "[]"),
                "notes": project.notes,
            },
            "price": _series(session, project_id, "price_usd"),
            "market_cap": _series(session, project_id, "market_cap"),
            "unlocked_total": _series(session, project_id, "unlocked_total"),
            "unlock_events": _events(session, project_id, "unlock"),
            "funding_events": _events(session, project_id, "funding"),
            # GitHub (ф.5): ядро — коммиты и активные разработчики; экосистема — новые репо по топику;
            # для каждого — ряды остальных монет пула за 2 года («vs аналоги»)
            "github_commits_week": _series(session, project_id, "github_commits_week"),
            "github_peers": _peers(session, project_id, "github_commits_week"),
            "github_active_devs_week": _series(session, project_id, "github_active_devs_week"),
            "github_devs_peers": _peers(session, project_id, "github_active_devs_week"),
            "github_eco_new_repos_week": _series(session, project_id, "github_eco_new_repos_week"),
            "github_eco_peers": _peers(session, project_id, "github_eco_new_repos_week"),
            "trends_weekly": _series(session, project_id, "trends_weekly"),
            "trends_monthly": _series(session, project_id, "trends_monthly"),
            "media_mentions": _series(session, project_id, "media_mentions"),
            "recent_mentions": [
                {
                    "ts": m.ts.isoformat(),
                    "title": m.title,
                    "url": m.url,
                    "domain": m.domain,
                    "is_pr": m.is_pr,
                }
                for m in recent_mentions
            ],
            "chain_tvl": _series(session, project_id, "chain_tvl_usd"),
            "stablecoins_mcap": _series(session, project_id, "stablecoins_usd"),
            "dex_volume": _series(session, project_id, "dex_volume_usd"),
            "chain_fees": _series(session, project_id, "chain_fees_usd"),
            "node_count": _series(session, project_id, "node_count"),
            "exchange_validators": _series(session, project_id, "exchange_validators_count"),
            "validator_events": _events(session, project_id, "validator"),
            "team_outflow_tokens": _series(session, project_id, "team_outflow_tokens"),
            "team_to_exchange_tokens": _series(session, project_id, "team_to_exchange_tokens"),
            "top_outflows": [
                {
                    "ts": f.ts.isoformat(),
                    "amount": f.amount,
                    "token": f.token,
                    "to_exchange": f.to_exchange,
                    "counterparty": f.counterparty,
                    "wallet": w.name or w.address[:10] + "…",
                    "tx_hash": f.tx_hash,
                }
                for f, w in top_outflows
            ],
            "smart_money_fresh_share": _series(session, project_id, "smart_money_fresh_share"),
            "discord_messages_week": _series(session, project_id, "discord_messages_week"),
            "discord_substantive_week": _series(session, project_id, "discord_substantive_week"),
            "ceo_tweets_week": _series(session, project_id, "ceo_tweets_week"),
            "twitter_curation": curation,
        }
    finally:
        session.close()


@app.get("/api/screener")
def screener_candidates():
    session = SessionLocal()
    try:
        latest = session.query(func.max(ScreenerCandidate.snapshot_date)).scalar()
        if latest is None:
            return {"snapshot_date": None, "candidates": []}
        rows = (
            session.query(ScreenerCandidate)
            .filter(ScreenerCandidate.snapshot_date == latest)
            .order_by(ScreenerCandidate.market_cap.desc())
            .all()
        )
        approved = {p.coingecko_id for p in session.query(Project).all()}
        return {
            "snapshot_date": latest.isoformat(),
            "candidates": [
                {
                    "coingecko_id": c.coingecko_id,
                    "name": c.name,
                    "symbol": c.symbol,
                    "market_cap": c.market_cap,
                    "fdv": c.fdv,
                    "volume_24h": c.volume_24h,
                    "categories": c.categories,
                    "reason": c.reason,
                    "in_pool": c.coingecko_id in approved,
                }
                for c in rows
            ],
        }
    finally:
        session.close()


@app.get("/api/ladder")
def ladder():
    session = SessionLocal()
    try:
        return compute_ladder(session)
    finally:
        session.close()
