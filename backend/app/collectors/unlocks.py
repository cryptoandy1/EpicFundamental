"""Разлоки (ф.7).

DefiLlama перенёс emissions в Pro-тариф (402 на бесплатном) — поэтому:
  1) если задан DEFILLAMA_API_KEY (env) — полный график из pro-api.llama.fi
     (кумулятивная эмиссия + клифы past+future);
  2) всегда подмешиваем ручной график клифов из projects.yaml:
       unlock_events:
         - { date: 2026-10-30, tokens: 175000000, note: "team+investors cliff" }
     (расписания публичны: token.unlocks.app, cryptorank.io/unlocks — руками).
Будущие события тоже сохраняем — они ложатся оверлеем на график цены.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from ..config import load_config
from ..models import Project
from . import register
from .base import Collector, upsert_events, upsert_metrics

log = logging.getLogger("collectors.unlocks")

CLIFF_THRESHOLD = 0.001  # 0.1% максимальной эмиссии за шаг = заметный клиф


def _pro_base() -> str | None:
    key = os.environ.get("DEFILLAMA_API_KEY", "")
    return f"https://pro-api.llama.fi/{key}/api" if key else None


@register
class UnlocksCollector(Collector):
    name = "unlocks"
    scope = "project"
    description = "Разлоки past+future: ручной график в projects.yaml + DefiLlama Pro при ключе (ф.7)"

    _index: list[dict] | None = None

    def _manual_events(self, project: Project) -> list[dict]:
        for p in load_config().get("projects", []):
            if p.get("id") == project.id:
                return p.get("unlock_events", []) or []
        return []

    def backfill(self, project: Project | None = None) -> str:
        assert project is not None
        parts = []

        manual = self._manual_events(project)
        manual_rows = [
            {
                "project_id": project.id,
                "type": "unlock",
                "ts": datetime.fromisoformat(str(e["date"])),
                "title": e.get("note", "Разлок"),
                "value": float(e.get("tokens", 0)),
                "meta": json.dumps({"source": "manual"}, ensure_ascii=False),
            }
            for e in manual
        ]
        if manual_rows:
            upsert_events(self.session, manual_rows)
            parts.append(f"{len(manual_rows)} клифов вручную")

        base = _pro_base()
        if base is not None:
            parts.append(self._from_defillama(project, base))
        elif not manual_rows:
            return (
                "0 разлоков: DefiLlama emissions теперь платный — добавьте unlock_events "
                "в projects.yaml (token.unlocks.app) или DEFILLAMA_API_KEY"
            )
        return "; ".join(parts)

    # --- DefiLlama Pro ---

    def _find_protocol(self, project: Project, base: str) -> str | None:
        if UnlocksCollector._index is None:
            UnlocksCollector._index = self.http.get_json(f"{base}/emissions")
        for item in UnlocksCollector._index or []:
            if (
                item.get("gecko_id") == project.coingecko_id
                or (item.get("name") or "").lower() == project.name.lower()
            ):
                return item.get("name")
        return None

    def _from_defillama(self, project: Project, base: str) -> str:
        try:
            protocol = self._find_protocol(project, base)
            if not protocol:
                return "нет в DefiLlama emissions (у нативных монет без вестинга это норма)"
            raw = self.http.get_json(f"{base}/emission/{protocol.replace(' ', '-').lower()}")
            if isinstance(raw, dict) and isinstance(raw.get("body"), str):
                raw = json.loads(raw["body"])
        except Exception as e:  # noqa: BLE001
            return f"DefiLlama: ошибка ({e})"

        data = (raw.get("documentedData") or {}).get("data") or raw.get("data") or []
        totals: dict[int, float] = {}
        by_category: dict[int, dict[str, float]] = {}
        for cat in data:
            label = cat.get("label", "")
            for point in cat.get("data", []):
                ts = int(point["timestamp"])
                unlocked = float(point.get("unlocked") or 0)
                totals[ts] = totals.get(ts, 0.0) + unlocked
                by_category.setdefault(ts, {})[label] = unlocked
        if not totals:
            return f"emissions '{protocol}': пустые данные"

        max_supply = max(totals.values())
        metric_rows, event_rows = [], []
        prev_total = 0.0
        for ts_unix in sorted(totals):
            ts = datetime.fromtimestamp(ts_unix, tz=timezone.utc).replace(tzinfo=None)
            total = totals[ts_unix]
            metric_rows.append(
                {"project_id": project.id, "metric": "unlocked_total", "ts": ts, "value": total}
            )
            delta = total - prev_total
            if max_supply > 0 and delta / max_supply >= CLIFF_THRESHOLD and prev_total > 0:
                event_rows.append(
                    {
                        "project_id": project.id,
                        "type": "unlock",
                        "ts": ts,
                        "title": f"Разлок {delta / max_supply * 100:.2f}% эмиссии",
                        "value": delta,
                        "meta": json.dumps(
                            {"categories": by_category.get(ts_unix, {}), "source": "defillama"},
                            ensure_ascii=False,
                        ),
                    }
                )
            prev_total = total

        n1 = upsert_metrics(self.session, metric_rows)
        n2 = upsert_events(self.session, event_rows)
        return f"'{protocol}': {n1} точек, {n2} клифов (включая будущие)"
