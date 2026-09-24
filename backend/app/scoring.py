"""Композитный скор «лесенки»: ранжирование пула для очередности входа.

Каждый фактор нормализуется в перцентиль 0..100 по пулу, затем взвешенная
сумма (веса — в projects.yaml -> scoring; отрицательный вес = штраф).
Факторы без данных не учитываются (скор считается по доступным).

Почти все факторы — моментум (среднее за 28 дней / среднее за предыдущие 84):
для очерёдности входа важно ускорение, а не масштаб. GitHub разделён на
github_core_devs (уникальные активные разработчики ядра в неделю, 28д/84д) и
github_ecosystem (новые репозитории с топиком экосистемы в неделю, 84д/168д с
порогом объёма — у малых экосистем счётчик редкий). Факторы Nansen (ф.11) —
средние недельных снапшотов за 28 дней: потоки нормируются на капитализацию,
чтобы монеты разного размера были сравнимы.

Фактор без данных получает НЕЙТРАЛЬНЫЙ перцентиль 50 и участвует в скоре с полным
весом: «не знаем» не должно ни помогать, ни вредить. (Раньше такой фактор выпадал
из знаменателя, что неявно приписывало ему средний уровень самой монеты по остальным
факторам.) Доля веса, стоящая на реальных данных, возвращается как `coverage`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .config import load_config
from .models import MARKET, Event, Metric, Project, Wallet, WalletFlow


NEUTRAL_PERCENTILE = 50.0  # перцентиль фактора без данных: «не знаем» = ни плюс, ни минус
ECO_MIN_BASE_REPOS = 15  # минимум новых репо за базовые 24 недели, чтобы считать моментум экосистемы


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _series_sum(
    session: Session, project_id: str, metric: str, since: datetime, as_of: datetime | None = None
) -> float | None:
    q = session.query(Metric.value).filter(
        Metric.project_id == project_id, Metric.metric == metric, Metric.ts >= since
    )
    if as_of is not None:
        q = q.filter(Metric.ts <= as_of)
    rows = q.all()
    return sum(r[0] for r in rows) if rows else None


def _latest(
    session: Session, project_id: str, metric: str, as_of: datetime | None = None
) -> float | None:
    """Последнее значение метрики; с as_of — последнее НА ТУ ДАТУ (для бэктеста)."""
    q = session.query(Metric.value).filter(
        Metric.project_id == project_id, Metric.metric == metric
    )
    if as_of is not None:
        q = q.filter(Metric.ts <= as_of)
    row = q.order_by(Metric.ts.desc()).first()
    return row[0] if row else None


def _momentum(
    session: Session,
    project_id: str,
    metric: str,
    recent_days: int = 28,
    base_days: int = 84,
    min_base_total: float = 0.0,
    as_of: datetime | None = None,
) -> float | None:
    """Среднее за последние recent_days / среднее за base_days до них.

    min_base_total — минимальная СУММА значений в базовом окне: моментум редких
    счётчиков (1–2 события в квартал) — шум, а не сигнал; ниже порога -> None
    (фактор честно исключается из скора)."""
    now = as_of or _now()
    recent = (
        session.query(Metric.value)
        .filter(
            Metric.project_id == project_id,
            Metric.metric == metric,
            Metric.ts >= now - timedelta(days=recent_days),
            Metric.ts <= now,
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


def _avg_recent(
    session: Session, project_id: str, metric: str, days: int = 28, as_of: datetime | None = None
) -> float | None:
    """Среднее значений метрики за последние `days` (снапшоты Nansen — недельные)."""
    now = as_of or _now()
    rows = (
        session.query(Metric.value)
        .filter(
            Metric.project_id == project_id,
            Metric.metric == metric,
            Metric.ts >= now - timedelta(days=days),
            Metric.ts <= now,
        )
        .all()
    )
    return sum(r[0] for r in rows) / len(rows) if rows else None


def _per_market_cap(
    session: Session, project_id: str, metric: str, days: int = 28, as_of: datetime | None = None
) -> float | None:
    """Средний поток за окно, нормированный на капитализацию — сравнимо между монетами."""
    flow = _avg_recent(session, project_id, metric, days, as_of=as_of)
    cap = _latest(session, project_id, "market_cap", as_of=as_of)
    return flow / cap if flow is not None and cap else None


def _value_at(
    session: Session, project_id: str, metric: str, at: datetime, max_gap_days: int = 10
) -> float | None:
    """Значение метрики на дату: ближайшая точка не позже `at` и не старше max_gap_days.

    Ограничение по разрыву обязательно: без него монета с обрывом ряда сравнивалась бы
    с ценой полугодовой давности и давала бы фантастическую «силу»."""
    row = (
        session.query(Metric.value)
        .filter(
            Metric.project_id == project_id,
            Metric.metric == metric,
            Metric.ts <= at,
            Metric.ts >= at - timedelta(days=max_gap_days),
        )
        .order_by(Metric.ts.desc())
        .first()
    )
    return row[0] if row else None


def _rel_strength_btc(
    session: Session, project_id: str, days: int = 90, as_of: datetime | None = None
) -> float | None:
    """Сила монеты относительно BTC за `days`: (рост монеты / рост BTC) − 1.

    Ротация в альты имеет смысл только для тех, кто уже обгоняет BTC: ценовой моментум —
    самый устойчивый из известных крипто-факторов, а у нас его не было вовсе. Окно 90 дней
    согласовано с базой 84 дня у остальных моментумов; 30-дневное окно уже стоит в воротах
    входа (доля альтов, обгоняющих BTC), дублировать его здесь не нужно."""
    now = as_of or _now()
    then = now - timedelta(days=days)
    p_now = _latest(session, project_id, "price_usd", as_of=now)
    p_then = _value_at(session, project_id, "price_usd", then)
    b_now = _latest(session, MARKET, "btc_price_usd", as_of=now)
    b_then = _value_at(session, MARKET, "btc_price_usd", then)
    if not (p_now and p_then and b_now and b_then):
        return None
    return (p_now / p_then) / (b_now / b_then) - 1.0


def _fees_to_mcap(
    session: Session, project_id: str, days: int = 90, as_of: datetime | None = None
) -> float | None:
    """Годовые комиссии сети к капитализации — единственный фактор оценки стоимости.

    Все прочие факторы измеряют ускорение; этот отвечает на вопрос «сколько платят за
    доллар реального использования». Аналог P/E: выше — дешевле."""
    now = as_of or _now()
    fees = _series_sum(session, project_id, "chain_fees_usd", now - timedelta(days=days), as_of=now)
    cap = _latest(session, project_id, "market_cap", as_of=now)
    if fees is None or not cap:
        return None
    return fees * (365.0 / days) / cap


def _sm_perp_skew(
    session: Session, project_id: str, min_accounts: int = 5, as_of: datetime | None = None
) -> float | None:
    """Перекос позиций Smart Money на перпах; при когорте < min_accounts — шум, None."""
    longs = _latest(session, project_id, "nansen_perp_sm_longs_count", as_of=as_of) or 0
    shorts = _latest(session, project_id, "nansen_perp_sm_shorts_count", as_of=as_of) or 0
    if longs + shorts < min_accounts:
        return None
    return _avg_recent(session, project_id, "nansen_perp_sm_skew", as_of=as_of)


def _factor_values(
    session: Session, project: Project, as_of: datetime | None = None
) -> dict[str, float | None]:
    now = as_of or _now()
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
    unlocked_now = _latest(session, project.id, "unlocked_total", as_of=now)
    unlock_pressure = (
        sum(e.value for e in future_unlocks) / unlocked_now
        if future_unlocks and unlocked_now
        else (0.0 if unlocked_now else None)
    )

    # продажи команды: доля выведенного на биржи за 90д (в токенах)
    team_out = _series_sum(session, project.id, "team_to_exchange_tokens", q90, as_of=now)
    has_wallets = (
        session.query(Wallet).filter_by(project_id=project.id).first() is not None
    )
    team_selling = team_out if has_wallets and team_out is not None else None

    node_now = _latest(session, project.id, "node_count", as_of=now)
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
        "github_core_devs": _momentum(session, project.id, "github_active_devs_week", as_of=now),
        # экосистема: окна длиннее (12 нед / 24 нед) и порог базы — новые репо у малых
        # экосистем редки, недельный моментум 28/84 был бы случайным числом
        "github_ecosystem": _momentum(
            session, project.id, "github_eco_new_repos_week", 84, 168,
            min_base_total=ECO_MIN_BASE_REPOS, as_of=now,
        ),
        "trends_momentum": _momentum(session, project.id, "trends_weekly", as_of=now),
        "mentions_momentum": _momentum(session, project.id, "media_mentions", as_of=now),
        "node_growth": node_growth,
        "unlock_pressure": unlock_pressure,
        "team_selling": team_selling,
        # Nansen (ф.11): потоки за 7д на капитализацию (среднее снапшотов за 28д) и
        # перекос позиций Smart Money на перпах Hyperliquid
        "fresh_wallets_flow": _per_market_cap(
            session, project.id, "nansen_fi7d_fresh_wallets_netflow_usd", as_of=now
        ),
        "exchange_flow": _per_market_cap(
            session, project.id, "nansen_fi7d_exchange_netflow_usd", as_of=now
        ),
        "sm_perp_skew": _sm_perp_skew(session, project.id, as_of=now),
        "discord_activity": _momentum(session, project.id, "discord_substantive_week", 28, 56, as_of=now),
        # DefiLlama (бесплатные эндпоинты): деньги и использование сети
        "tvl_momentum": _momentum(session, project.id, "chain_tvl_usd", as_of=now),
        "fees_momentum": _momentum(session, project.id, "chain_fees_usd", as_of=now),
        # Цена: единственный фактор, который смотрит на саму котировку, а не на активность
        "rel_strength_btc": _rel_strength_btc(session, project.id, as_of=now),
        # Оценка стоимости: годовые комиссии к капитализации (аналог P/E, выше = дешевле)
        "fees_to_mcap": _fees_to_mcap(session, project.id, as_of=now),
    }


def _percentile(values: list[float], v: float) -> float:
    if len(values) <= 1:
        return 50.0
    below = sum(1 for x in values if x < v)
    return below / (len(values) - 1) * 100 if len(values) > 1 else 50.0


def compute_ladder(session: Session, as_of: datetime | None = None) -> list[dict]:
    """[{project, score, coverage, factors: {name: {value, percentile, weight}}}] по убыванию скора.

    coverage — доля суммы |весов|, стоящая на реальных данных (остальное — нейтральные 50).
    as_of — считать скор ПО СОСТОЯНИЮ НА ДАТУ, игнорируя всё, что появилось позже
    (нужно бэктесту: иначе ранги знали бы будущее)."""
    weights = (load_config().get("scoring") or {})
    projects = session.query(Project).filter_by(approved=True).all()
    raw = {p.id: _factor_values(session, p, as_of=as_of) for p in projects}

    results = []
    for p in projects:
        total, weight_sum, known_weight = 0.0, 0.0, 0.0
        factors = {}
        for factor, weight in weights.items():
            value = raw[p.id].get(factor)
            pool = [raw[o.id][factor] for o in projects if raw[o.id].get(factor) is not None]
            weight_sum += abs(weight)
            if value is None or not pool:
                # нет данных — нейтральные 50, вес учитывается (не помогает и не вредит)
                total += NEUTRAL_PERCENTILE * abs(weight)
                factors[factor] = {
                    "value": None,
                    "percentile": None,
                    "imputed": NEUTRAL_PERCENTILE,
                    "weight": weight,
                }
                continue
            pct = _percentile(pool, value)
            # отрицательный вес: высокий перцентиль (много разлоков/продаж) — плохо
            total += (pct if weight >= 0 else (100 - pct)) * abs(weight)
            known_weight += abs(weight)
            factors[factor] = {"value": value, "percentile": round(pct, 1), "weight": weight}
        score = round(total / weight_sum, 1) if weight_sum else None
        available = sum(1 for f in factors.values() if f.get("percentile") is not None)
        results.append(
            {
                "project": p.id,
                "name": p.name,
                "symbol": p.symbol,
                "score": score,
                "coverage": round(known_weight / weight_sum, 3) if weight_sum else 0.0,
                "factors_available": available,
                "factors_total": len(factors),
                "factors": factors,
            }
        )
    results.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return results
