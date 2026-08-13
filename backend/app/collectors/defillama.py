"""Бесплатный DefiLlama — он-чейн экономика сети проекта (дополняет ф.8).

Без ключа и авторизации доступны полные исторические ряды:
- TVL сети              api.llama.fi/v2/historicalChainTvl/{chain}
- Стейблкоины на сети   stablecoins.llama.fi/stablecoincharts/{chain}
- DEX-объёмы            api.llama.fi/overview/dexs/{chain}
- Комиссии сети         api.llama.fi/overview/fees/{chain}

Pro-эндпоинты (raises/emissions) остаются платными — см. funding.py/unlocks.py.
Ряды из одних нулей (TVL у DA-сети Celestia) не сохраняются.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..models import Project
from . import register
from .base import Collector, upsert_metrics

log = logging.getLogger("collectors.defillama")

LLAMA = "https://api.llama.fi"
STABLES = "https://stablecoins.llama.fi"

# project.chain -> слаг DefiLlama, если отличается
CHAIN_SLUGS: dict[str, str] = {}


def _day(epoch: int | str) -> datetime:
    ts = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    return ts.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


@register
class DefiLlamaCollector(Collector):
    name = "defillama"
    scope = "project"
    description = "DefiLlama (бесплатно): TVL сети, стейблкоины, DEX-объёмы, комиссии"

    def backfill(self, project: Project | None = None) -> str:
        assert project is not None
        chain = CHAIN_SLUGS.get(project.chain, project.chain or "").lower()
        if not chain:
            return "нет chain в конфиге"
        return "; ".join(
            [
                self._tvl(project, chain),
                self._stables(project, chain),
                self._overview(project, chain, "dexs", "dex_volume_usd"),
                self._overview(project, chain, "fees", "chain_fees_usd"),
            ]
        )

    def _store(self, project: Project, metric: str, points: list[tuple[datetime, float]]) -> str:
        if not points:
            return f"{metric}: нет данных"
        if all(v == 0 for _, v in points):
            return f"{metric}: нулевой ряд — пропущен"
        n = upsert_metrics(
            self.session,
            [
                {"project_id": project.id, "metric": metric, "ts": ts, "value": v}
                for ts, v in points
            ],
        )
        return f"{metric}: {n} точек"

    def _tvl(self, project: Project, chain: str) -> str:
        try:
            data = self.http.get_json(f"{LLAMA}/v2/historicalChainTvl/{chain}")
        except Exception as e:  # noqa: BLE001 — один эндпоинт не роняет прогон
            log.warning("tvl %s: %s", chain, e)
            return f"chain_tvl_usd: ошибка ({e})"
        points = [(_day(row["date"]), float(row.get("tvl") or 0)) for row in data]
        return self._store(project, "chain_tvl_usd", points)

    def _stables(self, project: Project, chain: str) -> str:
        try:
            data = self.http.get_json(f"{STABLES}/stablecoincharts/{chain}")
        except Exception as e:  # noqa: BLE001
            log.warning("stables %s: %s", chain, e)
            return f"stablecoins_usd: ошибка ({e})"
        points = []
        for row in data:
            usd = row.get("totalCirculatingUSD") or {}
            points.append((_day(row["date"]), sum(float(v) for v in usd.values())))
        return self._store(project, "stablecoins_usd", points)

    def _overview(self, project: Project, chain: str, kind: str, metric: str) -> str:
        try:
            data = self.http.get_json(
                f"{LLAMA}/overview/{kind}/{chain}",
                params={"excludeTotalDataChart": "false", "excludeTotalDataChartBreakdown": "true"},
            )
        except Exception as e:  # noqa: BLE001 — у DA-сетей нет DEX/fees, это норма
            log.warning("%s %s: %s", kind, chain, e)
            return f"{metric}: недоступно ({type(e).__name__})"
        chart = data.get("totalDataChart") or []
        points = [(_day(ts), float(v or 0)) for ts, v in chart]
        return self._store(project, metric, points)
