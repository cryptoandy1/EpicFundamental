"""Оповещения о СИГНАЛАХ (не о сбоях прогона) — `python -m app notify`.

Дашборд показывает состояние, только когда в него смотрят. Сигналы редкие: ворота входа
открываются раз в цикл, сигнал выхода — один раз. Поэтому шлём в Telegram ТОЛЬКО переходы
(закрыты -> разогрев -> открыты, ok -> warming -> sell, протухание источника) плюс
воскресный дайджест. Иначе ежедневное «всё по-прежнему» перестают читать.

Отдельно от Send-Alert в update_common.ps1: тот кричит о сбое ПРОГОНА, этот — о смене
РЫНОЧНОГО состояния. Ключи Telegram общие.

Шаг нефатальный: без токена печатаем в stdout, любая ошибка внутри — код возврата 0,
чтобы оповещение никогда не роняло сбор данных.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

log = logging.getLogger("notify")

STATE_PATH = Path(__file__).resolve().parents[1] / "logs" / "signals_state.json"
DASHBOARD_URL = "https://cryptoandy1.github.io/EpicFundamental/"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
STATE_VERSION = 1

GATE_NAMES = {"closed": "закрыты", "warming": "разогрев", "open": "ОТКРЫТЫ"}
TIER_NAMES = {"ok": "спокойно", "warming": "разогрев", "sell": "ПРОДАВАТЬ"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def collect_payloads() -> tuple[dict, dict, list[dict]]:
    """Свежие ответы API напрямую, без HTTP (как это делает export)."""
    from .db import init_db
    from .main import ladder, market_overview, meta

    init_db()
    return market_overview(), meta(), ladder()


def snapshot_state(overview: dict, meta_payload: dict, now: datetime) -> dict:
    gate = overview.get("entry_gate") or {}
    exit_signal = overview.get("exit_signal") or {}
    playbook = overview.get("playbook") or {}
    return {
        "version": STATE_VERSION,
        "updated_at": now.replace(microsecond=0).isoformat(),
        "exit_tier": exit_signal.get("tier", "ok"),
        "gate_state": gate.get("gate_state", "closed"),
        "playbook": playbook.get("state", "HOLD_BTC"),
        "stale": sorted(s["metric"] for s in meta_payload.get("stale", [])),
        "trends_percentile": exit_signal.get("trends_percentile"),
        "coinbase_rank": exit_signal.get("coinbase_rank_overall_latest"),
        "alts_pct": gate.get("alts_beating_btc_30d_pct"),
        "dominance_pct": gate.get("btc_dominance_pct"),
        "history_days": gate.get("history_days", 0),
        "lookback_days": gate.get("dominance_lookback_days", 28),
    }


def diff_transitions(prev: dict | None, cur: dict, overview: dict) -> list[str]:
    """Сообщения только про то, что ИЗМЕНИЛОСЬ с прошлого запуска."""
    if not prev:
        return []
    out: list[str] = []
    playbook_text = (overview.get("playbook") or {}).get("text", "")

    if prev.get("gate_state") != cur["gate_state"]:
        was, now_ = GATE_NAMES.get(prev.get("gate_state", "?"), "?"), GATE_NAMES[cur["gate_state"]]
        head = "🚪 ВОРОТА ВХОДА ОТКРЫЛИСЬ" if cur["gate_state"] == "open" else f"🚪 Ворота входа: {was} → {now_}"
        out.append(
            f"{head}\n"
            f"Альтов обгоняют BTC: {cur['alts_pct']}% (порог из конфига)\n"
            f"Доминация BTC: {_fmt(cur['dominance_pct'])}%, истории {cur['history_days']} из {cur['lookback_days']} дн.\n"
            f"{playbook_text}\n{DASHBOARD_URL}"
        )

    if prev.get("exit_tier") != cur["exit_tier"]:
        was, now_ = TIER_NAMES.get(prev.get("exit_tier", "?"), "?"), TIER_NAMES[cur["exit_tier"]]
        reasons = "; ".join((overview.get("exit_signal") or {}).get("reasons", [])) or "пороги не достигнуты"
        head = "🔴 СИГНАЛ ВЫХОДА" if cur["exit_tier"] == "sell" else f"Сигнал выхода: {was} → {now_}"
        out.append(
            f"{head}\n{reasons}\n"
            f"Google Trends BTC: {cur['trends_percentile']} перцентиль, Coinbase: {_rank(cur['coinbase_rank'])}\n"
            f"{playbook_text}\n{DASHBOARD_URL}"
        )

    became = [m for m in cur["stale"] if m not in prev.get("stale", [])]
    healed = [m for m in prev.get("stale", []) if m not in cur["stale"]]
    if became:
        out.append(f"⚠️ Источник протух: {', '.join(became)}. Данные дашборда неполные.\n{DASHBOARD_URL}")
    if healed:
        out.append(f"✅ Источник снова свежий: {', '.join(healed)}.")
    return out


def build_digest(cur: dict, overview: dict, ladder_rows: list[dict], title: str) -> str:
    playbook_text = (overview.get("playbook") or {}).get("text", "")
    top = ladder_rows[:5]
    lines = [
        f"{title}",
        "",
        f"➤ {playbook_text}",
        "",
        f"Ворота входа: {GATE_NAMES[cur['gate_state']]} "
        f"(альтов {cur['alts_pct']}%, доминация {_fmt(cur['dominance_pct'])}%, "
        f"истории {cur['history_days']} из {cur['lookback_days']} дн.)",
        f"Сигнал выхода: {TIER_NAMES[cur['exit_tier']]} "
        f"(Trends {cur['trends_percentile']} перцентиль, Coinbase {_rank(cur['coinbase_rank'])})",
        "",
        "Лесенка, топ-5:",
    ]
    for i, row in enumerate(top, 1):
        score = f"{row['score']:.1f}" if row.get("score") is not None else "н/д"
        lines.append(f"  {i}. {row['symbol']} {score} (покрытие {round((row.get('coverage') or 0) * 100)}%)")
    if cur["stale"]:
        lines += ["", f"Протухшие источники: {', '.join(cur['stale'])}"]
    lines += ["", DASHBOARD_URL]
    return "\n".join(lines)


def send_telegram(text: str, token: str, chat_id: str) -> None:
    import httpx

    resp = httpx.post(
        TELEGRAM_API.format(token=token),
        data={"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"},
        timeout=20.0,
    )
    resp.raise_for_status()


def _fmt(v: float | None) -> str:
    return f"{v:.1f}" if isinstance(v, (int, float)) else "н/д"


def _rank(v: float | None) -> str:
    if v is None:
        return "н/д"
    return "вне топ-200" if v > 200 else f"#{v:g}"


def _load_state(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_notify(
    *,
    digest: bool = False,
    dry_run: bool = False,
    now: datetime | None = None,
    state_path: Path | None = None,
    payloads: tuple[dict, dict, list[dict]] | None = None,
    sender: Callable[[str], None] | None = None,
) -> list[str]:
    """Отправляет сообщения о переходах; возвращает отправленные тексты (для тестов)."""
    now = now or _now()
    path = state_path or STATE_PATH
    overview, meta_payload, ladder_rows = payloads or collect_payloads()
    cur = snapshot_state(overview, meta_payload, now)
    prev = _load_state(path)

    if prev is None:
        # первый запуск: не выдумываем переходы из пустоты, шлём одну сводку
        messages = [build_digest(cur, overview, ladder_rows, "EpicFundamental: слежение за сигналами включено")]
    else:
        messages = diff_transitions(prev, cur, overview)
        is_sunday = now.weekday() == 6
        already = prev.get("last_digest_date") == now.date().isoformat()
        if digest or (is_sunday and not already):
            messages.append(build_digest(cur, overview, ladder_rows, "EpicFundamental: недельная сводка"))

    sends_digest = any("сводка" in m or "включено" in m for m in messages)
    cur["last_digest_date"] = (
        now.date().isoformat() if sends_digest else (prev or {}).get("last_digest_date")
    )

    if not messages:
        if not dry_run:
            _save(path, cur)
        print("notify: изменений нет")
        return []

    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    for text in messages:
        if dry_run:
            print(f"--- (dry-run) ---\n{text}")
            continue
        if sender is not None:
            sender(text)
        elif token and chat_id:
            try:
                send_telegram(text, token, chat_id)
            except Exception as e:  # noqa: BLE001
                # состояние НЕ сохраняем: переход повторится при следующем прогоне
                log.warning("Telegram недоступен (%s) — состояние не сохранено, повторим завтра", e)
                print(f"notify: отправка не удалась ({e})")
                return []
        else:
            print(f"--- (нет TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) ---\n{text}")

    if not dry_run:
        _save(path, cur)
    return messages


def _save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
