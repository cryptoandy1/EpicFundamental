"""Два фактора, добавленных 24.09.2026: цена относительно BTC и оценка стоимости.

Все прочие факторы меряют ускорение активности. Эти отвечают на вопросы «обгоняет ли
монета биткоин» и «сколько платят за доллар реального использования».
"""
from __future__ import annotations

from datetime import timedelta

from app import scoring
from app.models import MARKET, Metric


def _add(session, project_id: str, metric: str, days_ago: int, value: float) -> None:
    session.add(
        Metric(
            project_id=project_id,
            metric=metric,
            ts=scoring._now() - timedelta(days=days_ago),
            value=value,
        )
    )


# --- сила относительно BTC ----------------------------------------------

def test_rel_strength_beats_btc(session, project):
    """Монета +100%, BTC +50% -> (2.0 / 1.5) - 1 = 0.333."""
    _add(session, project.id, "price_usd", 90, 100.0)
    _add(session, project.id, "price_usd", 0, 200.0)
    _add(session, MARKET, "btc_price_usd", 90, 100.0)
    _add(session, MARKET, "btc_price_usd", 0, 150.0)
    session.commit()

    assert round(scoring._rel_strength_btc(session, project.id), 4) == 0.3333


def test_rel_strength_negative_when_lagging_btc(session, project):
    _add(session, project.id, "price_usd", 90, 100.0)
    _add(session, project.id, "price_usd", 0, 110.0)
    _add(session, MARKET, "btc_price_usd", 90, 100.0)
    _add(session, MARKET, "btc_price_usd", 0, 150.0)
    session.commit()

    assert scoring._rel_strength_btc(session, project.id) < 0


def test_rel_strength_none_without_btc_history(session, project):
    _add(session, project.id, "price_usd", 90, 100.0)
    _add(session, project.id, "price_usd", 0, 200.0)
    session.commit()

    assert scoring._rel_strength_btc(session, project.id) is None


def test_rel_strength_ignores_too_old_point(session, project):
    """Обрыв ряда не должен превращаться в фантастическую «силу»: нужен свежий якорь."""
    _add(session, project.id, "price_usd", 200, 10.0)  # далеко за окном max_gap
    _add(session, project.id, "price_usd", 0, 200.0)
    _add(session, MARKET, "btc_price_usd", 90, 100.0)
    _add(session, MARKET, "btc_price_usd", 0, 150.0)
    session.commit()

    assert scoring._rel_strength_btc(session, project.id) is None


# --- комиссии к капитализации -------------------------------------------

def test_fees_to_mcap_annualizes(session, project):
    """90 дней по 10 = 900; годовые 900*365/90 = 3650; при капе 10 000 -> 0.365."""
    for d in range(90):
        _add(session, project.id, "chain_fees_usd", d, 10.0)
    _add(session, project.id, "market_cap", 0, 10_000.0)
    session.commit()

    assert round(scoring._fees_to_mcap(session, project.id), 4) == 0.365


def test_fees_to_mcap_none_without_fees(session, project):
    """Сеть без комиссий в DefiLlama -> фактор честно отсутствует (нейтральные 50)."""
    _add(session, project.id, "market_cap", 0, 10_000.0)
    session.commit()

    assert scoring._fees_to_mcap(session, project.id) is None


def test_both_factors_reach_the_ladder(session, project):
    """Фактор обязан появиться в выдаче compute_ladder, иначе вес уйдёт в никуда."""
    _add(session, project.id, "price_usd", 90, 100.0)
    _add(session, project.id, "price_usd", 0, 200.0)
    _add(session, MARKET, "btc_price_usd", 90, 100.0)
    _add(session, MARKET, "btc_price_usd", 0, 150.0)
    _add(session, project.id, "chain_fees_usd", 1, 100.0)
    _add(session, project.id, "market_cap", 0, 10_000.0)
    session.commit()

    factors = scoring.compute_ladder(session)[0]["factors"]
    assert factors["rel_strength_btc"]["value"] is not None
    assert factors["fees_to_mcap"]["value"] is not None
