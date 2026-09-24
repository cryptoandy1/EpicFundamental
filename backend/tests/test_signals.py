"""Сигнал выхода, ворота входа и playbook — чистая логика без сети и без БД, где возможно.

Ворота и сигнал отвечают на два РАЗНЫХ вопроса, и их нельзя смешивать:
entry_gate — когда ротировать BTC в альты, exit_signal — когда выходить из всего в стейблы.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app import main as m
from app.models import MARKET, Metric

CFG = {
    "trends_sell_pct": 90,
    "trends_warming_pct": 75,
    "coinbase_sell_rank": 10,
    "coinbase_warming_rank": 50,
}


# --- сигнал выхода -------------------------------------------------------

def test_exit_tier_ok_when_nobody_cares():
    r = m._exit_signal(4.9, 201.0, CFG)
    assert r["tier"] == "ok"
    assert r["reasons"] == []


def test_exit_tier_sell_by_trends():
    r = m._exit_signal(92.0, 201.0, CFG)
    assert r["tier"] == "sell"
    assert "Google Trends" in r["reasons"][0]


def test_exit_tier_sell_by_coinbase_alone():
    """Ранг Coinbase — независимый источник: до этапа 2 он только рисовался на графике."""
    r = m._exit_signal(10.0, 7.0, CFG)
    assert r["tier"] == "sell"
    assert any("Coinbase" in x for x in r["reasons"])


def test_exit_tier_warming_by_either_source():
    assert m._exit_signal(80.0, 201.0, CFG)["tier"] == "warming"
    assert m._exit_signal(10.0, 42.0, CFG)["tier"] == "warming"


def test_exit_tier_sell_wins_over_warming():
    r = m._exit_signal(95.0, 42.0, CFG)
    assert r["tier"] == "sell"


def test_exit_tier_handles_missing_data():
    assert m._exit_signal(None, None, CFG)["tier"] == "ok"


def test_percentile_of_latest_max_is_100():
    """Знаменатель n-1: максимум ряда обязан давать 100, иначе порог 90 недостижим."""
    series = [[f"2026-01-{i + 1:02d}T00:00:00", float(i)] for i in range(12)]
    assert m._percentile_of_latest(series) == 100.0
    assert m._percentile_of_latest(series[:5]) is None  # < 10 точек


# --- ворота входа --------------------------------------------------------

def _seed_dominance(session, values: list[float], end: datetime, step_days: int = 1) -> None:
    rows = [
        Metric(
            project_id=MARKET,
            metric="btc_dominance_pct",
            ts=end - timedelta(days=step_days * (len(values) - 1 - i)),
            value=v,
        )
        for i, v in enumerate(values)
    ]
    session.add_all(rows)
    session.commit()


def _seed_alts(session, value: float, end: datetime) -> None:
    session.add(Metric(project_id=MARKET, metric="alts_beating_btc_30d_pct", ts=end, value=value))
    session.commit()


@pytest.fixture()
def gate_cfg(monkeypatch):
    monkeypatch.setattr(
        m, "load_config", lambda: {"entry_gate": {"alts_threshold_pct": 50, "dominance_lookback_days": 28}}
    )


def test_gate_warming_when_history_too_short(session, gate_cfg):
    """Наш реальный случай на 24.09: альты 64.6% при пороге 50, но истории 2 дня."""
    end = datetime(2026, 9, 24)
    _seed_dominance(session, [58.8, 58.6], end)
    _seed_alts(session, 64.6, end)

    g = m._entry_gate(session)
    assert g["gate_state"] == "warming"
    assert g["open"] is False
    assert g["history_days"] == 2
    assert g["dominance_confirmed"] is False


def test_gate_open_when_dominance_falls_on_averages(session, gate_cfg):
    end = datetime(2026, 12, 1)
    values = [62.0] * 33 + [57.0] * 7  # последняя неделя заметно ниже базовой
    _seed_dominance(session, values, end)
    _seed_alts(session, 70.0, end)

    g = m._entry_gate(session)
    assert g["gate_state"] == "open"
    assert g["open"] is True
    assert g["dominance_confirmed"] is True
    assert g["dominance_recent_mean"] == 57.0
    assert g["dominance_base_mean"] == 62.0


def test_gate_closed_when_dominance_rises(session, gate_cfg):
    end = datetime(2026, 12, 1)
    _seed_dominance(session, [55.0] * 33 + [60.0] * 7, end)
    _seed_alts(session, 70.0, end)

    g = m._entry_gate(session)
    assert g["gate_state"] == "closed"
    assert g["dominance_falling"] is False


def test_gate_closed_when_alts_below_threshold(session, gate_cfg):
    end = datetime(2026, 12, 1)
    _seed_dominance(session, [62.0] * 33 + [57.0] * 7, end)
    _seed_alts(session, 31.0, end)

    assert m._entry_gate(session)["gate_state"] == "closed"


def test_gate_falls_back_to_two_points_when_sparse(session, gate_cfg):
    """Разреженные точки (реже раза в неделю): усреднять нечего, работает старая логика."""
    end = datetime(2026, 12, 1)
    _seed_dominance(session, [62.0, 61.0, 60.0, 57.0], end, step_days=14)
    _seed_alts(session, 70.0, end)

    g = m._entry_gate(session)
    assert g["dominance_recent_mean"] is None
    assert g["dominance_falling"] is True
    assert g["gate_state"] == "open"


# --- playbook ------------------------------------------------------------

def test_playbook_exit_beats_everything():
    p = m._playbook("sell", "open", 40, 28)
    assert p["state"] == "EXIT"
    assert "стейблы" in p["text"]


def test_playbook_rotate_when_gate_open_and_calm():
    p = m._playbook("ok", "open", 40, 28)
    assert p["state"] == "ROTATE"


def test_playbook_hold_mentions_both_warnings():
    p = m._playbook("warming", "warming", 2, 28)
    assert p["state"] == "HOLD_BTC"
    assert "план выхода" in p["text"]
    assert "2 из 28" in p["text"]
