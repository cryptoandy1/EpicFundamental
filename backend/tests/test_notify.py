"""Нотификатор: шлём ТОЛЬКО переходы, иначе ежедневное «всё по-прежнему» перестают читать."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app import notify


def _payloads(*, tier="ok", gate="closed", stale=("media_mentions",), playbook="Держать ядро в BTC."):
    overview = {
        "exit_signal": {
            "tier": tier,
            "trends_percentile": 4.9,
            "coinbase_rank_overall_latest": 201.0,
            "thresholds": {},
            "reasons": ["причина"] if tier != "ok" else [],
        },
        "entry_gate": {
            "gate_state": gate,
            "alts_beating_btc_30d_pct": 64.6,
            "btc_dominance_pct": 58.6,
            "history_days": 2,
            "dominance_lookback_days": 28,
        },
        "playbook": {"state": "HOLD_BTC", "text": playbook},
    }
    meta = {"stale": [{"metric": m} for m in stale]}
    ladder = [
        {"symbol": "INJ", "score": 61.2, "coverage": 0.48},
        {"symbol": "SUI", "score": 57.4, "coverage": 0.56},
    ]
    return overview, meta, ladder


@pytest.fixture()
def state_file(tmp_path):
    return tmp_path / "signals_state.json"


@pytest.fixture(autouse=True)
def _no_telegram(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


MONDAY = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)  # понедельник
SUNDAY = datetime(2026, 9, 27, 19, 0, tzinfo=timezone.utc)


def _run(state_file, sent, **kw):
    return notify.run_notify(
        state_path=state_file,
        sender=sent.append,
        now=kw.pop("now", MONDAY),
        payloads=kw.pop("payloads", _payloads()),
        **kw,
    )


def test_first_run_sends_one_digest_not_fake_transitions(state_file):
    sent: list[str] = []
    _run(state_file, sent)
    assert len(sent) == 1
    assert "слежение за сигналами включено" in sent[0]
    assert state_file.exists()


def test_quiet_day_sends_nothing(state_file):
    sent: list[str] = []
    _run(state_file, sent)
    sent.clear()
    _run(state_file, sent)
    assert sent == []


def test_gate_opening_is_announced(state_file):
    sent: list[str] = []
    _run(state_file, sent)
    sent.clear()
    _run(state_file, sent, payloads=_payloads(gate="open"))
    assert len(sent) == 1
    assert "ВОРОТА ВХОДА ОТКРЫЛИСЬ" in sent[0]
    assert notify.DASHBOARD_URL in sent[0]


def test_exit_signal_is_announced_loudly(state_file):
    sent: list[str] = []
    _run(state_file, sent)
    sent.clear()
    _run(state_file, sent, payloads=_payloads(tier="sell"))
    assert "СИГНАЛ ВЫХОДА" in sent[0]


def test_new_stale_source_is_announced(state_file):
    sent: list[str] = []
    _run(state_file, sent)
    sent.clear()
    _run(state_file, sent, payloads=_payloads(stale=("media_mentions", "trends_weekly")))
    assert len(sent) == 1
    assert "протух" in sent[0] and "trends_weekly" in sent[0]


def test_recovered_source_is_announced(state_file):
    sent: list[str] = []
    _run(state_file, sent)
    sent.clear()
    _run(state_file, sent, payloads=_payloads(stale=()))
    assert "снова свежий" in sent[0]


def test_sunday_digest_once_per_day(state_file):
    sent: list[str] = []
    _run(state_file, sent)          # инициализация в понедельник
    sent.clear()
    _run(state_file, sent, now=SUNDAY)
    assert len(sent) == 1 and "недельная сводка" in sent[0]
    sent.clear()
    _run(state_file, sent, now=SUNDAY)   # второй прогон в то же воскресенье
    assert sent == []


def test_dry_run_writes_nothing(state_file):
    sent: list[str] = []
    notify.run_notify(
        state_path=state_file, sender=sent.append, now=MONDAY, payloads=_payloads(), dry_run=True
    )
    assert not state_file.exists()
    assert sent == []


def test_failed_send_keeps_state_so_transition_repeats(state_file, monkeypatch):
    """Упавшая отправка не должна «съесть» переход: состояние не сохраняем."""
    sent: list[str] = []
    _run(state_file, sent)
    before = json.loads(state_file.read_text(encoding="utf-8"))

    def boom(_text):
        raise RuntimeError("сеть недоступна")

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setattr(notify, "send_telegram", lambda *a, **k: boom(None))
    out = notify.run_notify(
        state_path=state_file, now=MONDAY, payloads=_payloads(gate="open")
    )
    assert out == []
    assert json.loads(state_file.read_text(encoding="utf-8")) == before


def test_without_token_prints_and_still_saves(state_file, capsys):
    notify.run_notify(state_path=state_file, now=MONDAY, payloads=_payloads())
    assert "нет TELEGRAM_BOT_TOKEN" in capsys.readouterr().out
    assert state_file.exists()
