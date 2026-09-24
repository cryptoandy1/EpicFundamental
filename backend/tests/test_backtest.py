"""Бэктест: as-of скоринг не должен знать будущего, spearman должен считаться правильно."""
from __future__ import annotations

from datetime import datetime, timedelta

from app import scoring
from app.backtest import run_backtest, spearman
from app.models import MARKET, Metric, Project


def test_spearman_monotonic():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0


def test_spearman_handles_ties_and_short_input():
    assert spearman([1, 1, 2, 2], [5, 5, 9, 9]) == 1.0
    assert spearman([1, 2], [3, 4]) is None      # < 3 точек
    assert spearman([1, 1, 1], [1, 2, 3]) is None  # нулевая дисперсия


def test_as_of_ignores_the_future(session, project):
    """Главное свойство: ранг на дату не должен видеть точки, появившиеся позже."""
    now = scoring._now()
    session.add_all(
        [
            Metric(project_id=project.id, metric="price_usd", ts=now - timedelta(days=100), value=100.0),
            Metric(project_id=project.id, metric="price_usd", ts=now - timedelta(days=50), value=150.0),
            Metric(project_id=project.id, metric="price_usd", ts=now, value=900.0),  # «будущее»
            Metric(project_id=MARKET, metric="btc_price_usd", ts=now - timedelta(days=100), value=100.0),
            Metric(project_id=MARKET, metric="btc_price_usd", ts=now - timedelta(days=50), value=100.0),
            Metric(project_id=MARKET, metric="btc_price_usd", ts=now, value=100.0),
        ]
    )
    session.commit()

    as_of = now - timedelta(days=50)
    past = scoring._rel_strength_btc(session, project.id, days=50, as_of=as_of)
    present = scoring._rel_strength_btc(session, project.id, days=50)
    assert round(past, 3) == 0.5      # 150/100 при неизменном BTC
    assert present > past             # сегодня видно взлёт до 900


def test_run_backtest_ranks_winner_above_loser(session):
    """Ранжирование должно работать: у растущей монеты скор выше, лонг-шорт положительный.

    Монет минимум три — при меньшем числе ранжировать нечего и период пропускается."""
    now = scoring._now()
    start = now - timedelta(days=200)
    session.add_all(
        [
            Project(id="winner", name="Winner", symbol="WIN", approved=True),
            Project(id="middle", name="Middle", symbol="MID", approved=True),
            Project(id="loser", name="Loser", symbol="LOSE", approved=True),
        ]
    )
    rows = []
    for d in range(0, 201, 5):
        ts = start + timedelta(days=d)
        rows += [
            # winner обгоняет BTC, middle идёт вровень, loser отстаёт
            Metric(project_id="winner", metric="price_usd", ts=ts, value=100.0 * (1 + d / 100)),
            Metric(project_id="middle", metric="price_usd", ts=ts, value=100.0),
            Metric(project_id="loser", metric="price_usd", ts=ts, value=100.0 * (1 - d / 400)),
            Metric(project_id=MARKET, metric="btc_price_usd", ts=ts, value=100.0),
        ]
    session.add_all(rows)
    session.commit()

    result = run_backtest(session, start + timedelta(days=100), now, step_days=28, horizon_days=28, top_n=1)
    assert result["summary"]["periods"] >= 1
    assert result["summary"]["mean_long_short"] > 0
    assert all("caveats" not in d for d in result["dates"])
    assert result["summary"]["caveats"], "ограничения обязаны ехать вместе с числами"


def test_backtest_skips_dates_without_prices(session, project):
    """Нет цен — период честно пропускается, а не считается нулевым."""
    now = scoring._now()
    session.add(Metric(project_id=MARKET, metric="btc_price_usd", ts=now - timedelta(days=60), value=100.0))
    session.commit()
    result = run_backtest(session, now - timedelta(days=90), now, step_days=28, horizon_days=28)
    assert result["summary"]["periods"] == 0
