"""Композитный скор «лесенки»: ранжирование пула для очередности входа.

Каждый фактор нормализуется в перцентиль 0..100 по пулу, затем взвешенная
сумма (веса — в projects.yaml -> scoring; отрицательный вес = штраф).
Факторы без данных не учитываются (скор считается по доступным).

Почти все факторы — моментум (среднее за 28 дней / среднее за предыдущие 84):
для очерёдности входа важно ускорение, а не масштаб. GitHub разделён на
github_core_devs (уникальные активные разработчики ядра в неделю, 28д/84д) и
github_ecosystem (новые репозитории с топиком экосистемы в неделю, 84д/168д с
порогом объёма — у малых экосистем счётчик редкий).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .config import load_config
from .models import Event, Metric, Project, Wallet, WalletFlow


ECO_MIN_BASE_REPOS = 15  # минимум новых репо за базовые 24 недели, чтобы считать моментум экосистемы


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _series_sum(session: Session, project_id: str, metric: str, since: datetime) -> float | None:
    rows = (
        session.query(Metric.value)
        .filter(Metric.project_id == project_id, Metric.metric == metric, Metric.ts >= since)
        .all()
    )
    return sum(r[0] for r in rows) if rows else None


def _latest(session: Session, project_id: str, metric: str) -> float | None:
    row = (
        session.query(Metric.value)
        .filter(Metric.project_id == project_id, Metric.metric == metric)
        .order_by(Metric.ts.desc())
        .first()
    )
    return row[0] if row else None


def _momentum(
    session: Session,
    project_id: str,
    metric: str,
    recent_days: int = 28,
    base_days: int = 84,
    min_base_total: float = 0.0,
) -> float | None:
    """Среднее за последние recent_days / среднее за base_days до них.

    min_base_total — минимальная СУММА значений в базовом окне: моментум редких
    счётчиков (1–2 события в квартал) — шум, а не сигнал; ниже порога -> None
    (фактор честно исключается из скора)."""
    now = _now()
    recent = (
        session.query(Metric.value)
        .filter(
            Metric.project_id == project_id,
            Metric.metric == metric,
            Metric.ts >= now - timedelta(days=recent_days),
        )
        .all()
    )
    base = (
        session.query(Metric.value)
        .filter(
            Metric.project_id == project_id,
            Metric.metric == metric,
            Metric.ts >= now - timedelta(days=recent_days + base_days),
            Metric.ts < now - timedelta(days=recent_days),
        )
        .all()
    )
    if not recent or not base:
        return None
    if sum(r[0] for r in base) < min_base_total:
        return None
    recent_avg = sum(r[0] for r in recent) / len(recent)
    base_avg = sum(r[0] for r in base) / len(base)
    return recent_avg / base_avg if base_avg else None


def _factor_values(session: Session, project: Project) -> dict[str, float | None]:
    now = _now()
    q90 = now - timedelta(days=90)

    # навес разлоков: будущие клифы на 90 дней вперёд, суммарные токены / текущая эмиссия
    future_unlocks = (
        session.query(Event)
        .filter(
            Event.project_id == project.id,
            Event.type == "unlock",
            Event.ts >= now,
            Event.ts <= now + timedelta(days=90),
        )
        .all()
    )
    unlocked_now = _latest(session, project.id, "unlocked_total")
    unlock_pressure = (
        sum(e.value for e in future_unlocks) / unlocked_now
        if future_unlocks and unlocked_now
        else (0.0 if unlocked_now else None)
    )

    # продажи команды: доля выведенного на биржи за 90д (в токенах)
    team_out = _series_sum(session, project.id, "team_to_exchange_tokens", q90)
    has_wallets = (
        session.query(Wallet).filter_by(project_id=project.id).first() is not None
    )
    team_selling = team_out if has_wallets and team_out is not None else None

    node_now = _latest(session, project.id, "node_count")
    node_old = (
        session.query(Metric.value)
        .filter(
            Metric.project_id == project.id,
            Metric.metric == "node_count",
            Metric.ts <= q90,
        )
        .order_by(Metric.ts.desc())
        .first()
    )
    node_growth = node_now / node_old[0] if node_now and node_old and node_old[0] else None

    return {
        # GitHub (ф.5) — два фактора: ядро (разработка ОТ команды) и экосистема (разработка НА платформе)
        "github_core_devs": _momentum(session, project.id, "github_active_devs_week"),
        # экосистема: окна длиннее (12 нед / 24 нед) и порог базы — новые репо у малых
        # экосистем редки, недельный моментум 28/84 был бы случайным числом
        "github_ecosystem": _momentum(
            session, project.id, "github_eco_new_repos_week", 84, 168, min_base_total=ECO_MIN_BASE_REPOS
        ),
        "trends_momentum": _momentum(session, project.id, "trends_weekly"),
        "mentions_momentum": _momentum(session, project.id, "media_mentions"),
        "node_growth": node_growth,
        "unlock_pressure": unlock_pressure,
        "team_selling": team_selling,
        "smart_money": _latest(session, project.id, "smart_money_fresh_share"),
        "discord_activity": _momentum(session, project.id, "discord_substantive_week", 28, 56),
        # DefiLlama (бесплатные эндпоинты): деньги и использование сети
        "tvl_momentum": _momentum(session, project.id, "chain_tvl_usd"),
        "fees_momentum": _momentum(session, project.id, "chain_fees_usd"),
    }


def _percentile(values: list[float], v: float) -> float:
    if len(values) <= 1:
        return 50.0
    below = sum(1 for x in values if x < v)
    return below / (len(values) - 1) * 100 if len(values) > 1 else 50.0


def compute_ladder(session: Session) -> list[dict]:
    """[{project, score, factors: {name: {value, percentile, weight}}}] по убыванию скора."""
    weights = (load_config().get("scoring") or {})
    projects = session.query(Project).filter_by(approved=True).all()
    raw = {p.id: _factor_values(session, p) for p in projects}

    results = []
    for p in projects:
        total, weight_sum = 0.0, 0.0
        factors = {}
        for factor, weight in weights.items():
            value = raw[p.id].get(factor)
            pool = [raw[o.id][factor] for o in projects if raw[o.id].get(factor) is not None]
            if value is None or not pool:
                factors[factor] = {"value": None, "percentile": None, "weight": weight}
                continue
            pct = _percentile(pool, value)
            # отрицательный вес: высокий перцентиль (много разлоков/продаж) — плохо
            contribution = (pct if weight >= 0 else (100 - pct)) * abs(weight)
            total += contribution
            weight_sum += abs(weight)
            factors[factor] = {"value": value, "percentile": round(pct, 1), "weight": weight}
        score = round(total / weight_sum, 1) if weight_sum else None
        results.append(
            {
                "project": p.id,
                "name": p.name,
                "symbol": p.symbol,
                "score": score,
                "factors": factors,
            }
        )
    results.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return results
