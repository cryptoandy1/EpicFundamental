"""Фандинг-раунды (ф.4).

DefiLlama перенёс /raises в Pro-тариф (402 на бесплатном) — поэтому:
  1) если задан DEFILLAMA_API_KEY (env) — берём из pro-api.llama.fi;
  2) всегда подмешиваем ручные записи из projects.yaml:
       funding_rounds:
         - { date: 2021-06-09, round: "Series A", amount_usd: 314000000,
             investors: [a16z, Polychain] }
     (раунды публичны — 5 минут на монету в CryptoRank/Messari руками).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from ..config import load_config
from ..models import Project
from . import register
from .base import Collector, upsert_events

log = logging.getLogger("collectors.funding")


def _llama_raises_url() -> str | None:
    key = os.environ.get("DEFILLAMA_API_KEY", "")
    return f"https://pro-api.llama.fi/{key}/api/raises" if key else None


@register
class FundingCollector(Collector):
    name = "funding"
    scope = "project"
    description = "Фандинг-раунды: ручной ввод в projects.yaml + DefiLlama Pro при наличии ключа (ф.4)"

    _cache: list[dict] | None = None

    def _raises(self) -> list[dict]:
        if FundingCollector._cache is None:
            url = _llama_raises_url()
            if url is None:
                FundingCollector._cache = []
            else:
                try:
                    data = self.http.get_json(url)
                    FundingCollector._cache = data.get("raises", [])
                except Exception as e:  # noqa: BLE001
                    log.warning("DefiLlama raises: %s", e)
                    FundingCollector._cache = []
        return FundingCollector._cache

    def _manual_rounds(self, project: Project) -> list[dict]:
        for p in load_config().get("projects", []):
            if p.get("id") == project.id:
                return p.get("funding_rounds", []) or []
        return []

    def backfill(self, project: Project | None = None) -> str:
        assert project is not None
        rows = []

        needles = {project.name.lower(), project.id.lower()}
        for r in self._raises():
            if (r.get("name") or "").lower() not in needles:
                continue
            ts = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).replace(tzinfo=None)
            investors = (r.get("leadInvestors") or []) + (r.get("otherInvestors") or [])
            rows.append(
                {
                    "project_id": project.id,
                    "type": "funding",
                    "ts": ts,
                    "title": r.get("round") or "Round",
                    "value": float(r.get("amount") or 0) * 1_000_000,  # llama хранит в $M
                    "meta": json.dumps({"investors": investors, "source": "defillama"}, ensure_ascii=False),
                }
            )

        manual = self._manual_rounds(project)
        for r in manual:
            rows.append(
                {
                    "project_id": project.id,
                    "type": "funding",
                    "ts": datetime.fromisoformat(str(r["date"])),
                    "title": r.get("round", "Round"),
                    "value": float(r.get("amount_usd", 0)),
                    "meta": json.dumps(
                        {"investors": r.get("investors", []), "source": "manual"}, ensure_ascii=False
                    ),
                }
            )

        n = upsert_events(self.session, rows)
        if not rows and not os.environ.get("DEFILLAMA_API_KEY"):
            return (
                "0 раундов: DefiLlama /raises теперь платный — добавьте funding_rounds "
                "в projects.yaml вручную (или DEFILLAMA_API_KEY)"
            )
        return f"funding: {n} раундов ({len(manual)} вручную)"
