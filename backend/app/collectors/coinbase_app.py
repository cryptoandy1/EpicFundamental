"""Ранг приложения Coinbase в App Store (ф.2) — прокси скачиваний.

Бесплатных данных о числе скачиваний нет (SensorTower/data.ai — платные).
Прокси-сигнал: позиция Coinbase в топ-чартах App Store. На пиках маний
Coinbase влетает в топ-10 общего топа — это и есть сигнал.

Бэкфилл ограниченный: снапшоты старого RSS-эндпоинта в web.archive.org
(помесячно, сколько сохранилось).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..models import MARKET, Project
from . import register
from .base import Collector, upsert_metrics

log = logging.getLogger("collectors.coinbase")

COINBASE_APP_ID = "886427730"
OUT_OF_CHART = 201.0  # «за пределами топ-200»

V2_TOP_FREE = "https://rss.marketingtools.apple.com/api/v2/us/apps/top-free/{limit}/apps.json"
LEGACY_FINANCE_RSS = "https://itunes.apple.com/us/rss/topfreeapplications/limit=200/genre=6015/json"
CDX = "http://web.archive.org/cdx/search/cdx"
MAX_SNAPSHOTS = 120


def _rank_from_v2(data: dict) -> float:
    for i, app in enumerate((data.get("feed") or {}).get("results", []), start=1):
        if str(app.get("id")) == COINBASE_APP_ID:
            return float(i)
    return OUT_OF_CHART


def _rank_from_legacy(data: dict) -> float:
    for i, entry in enumerate((data.get("feed") or {}).get("entry", []) or [], start=1):
        app_id = ((entry.get("id") or {}).get("attributes") or {}).get("im:id", "")
        if str(app_id) == COINBASE_APP_ID:
            return float(i)
    return OUT_OF_CHART


@register
class CoinbaseAppCollector(Collector):
    name = "coinbase_app"
    scope = "market"
    description = "Ранг Coinbase в App Store: общий топ + Finance; бэкфилл из web.archive.org (ф.2)"

    def backfill(self, project: Project | None = None) -> str:
        parts = []
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

        for limit in (200, 100):  # Apple периодически 500-ит на limit=200
            try:
                rank = _rank_from_v2(self.http.get_json(V2_TOP_FREE.format(limit=limit)))
                upsert_metrics(
                    self.session,
                    [{"project_id": MARKET, "metric": "coinbase_rank_overall", "ts": today, "value": rank}],
                )
                parts.append(f"общий топ: {'#%d' % rank if rank < OUT_OF_CHART else f'вне топ-{limit}'}")
                break
            except Exception as e:  # noqa: BLE001
                log.warning("v2 top-free limit=%d: %s", limit, e)

        try:
            rank = _rank_from_legacy(self.http.get_json(LEGACY_FINANCE_RSS))
            upsert_metrics(
                self.session,
                [{"project_id": MARKET, "metric": "coinbase_rank_finance", "ts": today, "value": rank}],
            )
            parts.append(f"Finance: {'#%d' % rank if rank < OUT_OF_CHART else 'вне топ-200'}")
        except Exception as e:  # noqa: BLE001
            log.warning("legacy finance rss: %s", e)

        parts.append(self._wayback_backfill())
        return "; ".join(parts)

    def _wayback_backfill(self) -> str:
        """Помесячные снапшоты старого Finance-RSS из архива интернета."""
        from ..models import Metric

        # история из архива уже накачана — не мучаем archive.org повторно
        wayback_points = (
            self.session.query(Metric)
            .filter(Metric.metric == "coinbase_rank_finance", Metric.meta != "")
            .count()
        )
        if wayback_points >= 12:
            return f"wayback: уже есть {wayback_points} точек, пропуск"
        try:
            rows = self.http.get_json(
                CDX,
                params={
                    "url": "itunes.apple.com/us/rss/topfreeapplications/limit=200/genre=6015/json",
                    "output": "json",
                    "filter": "statuscode:200",
                    "collapse": "timestamp:6",  # по месяцу
                    "from": "2017",
                },
            )
        except Exception as e:  # noqa: BLE001
            return f"wayback: недоступен ({e})"
        snapshots = rows[1:][:MAX_SNAPSHOTS] if rows else []
        stored = 0
        for snap in snapshots:
            ts_str, original = snap[1], snap[2]
            ts = datetime.strptime(ts_str, "%Y%m%d%H%M%S")
            try:
                data = self.http.get_json(f"https://web.archive.org/web/{ts_str}id_/{original}")
                rank = _rank_from_legacy(data)
            except Exception:  # noqa: BLE001 — битые снапшоты пропускаем
                continue
            upsert_metrics(
                self.session,
                [
                    {
                        "project_id": MARKET,
                        "metric": "coinbase_rank_finance",
                        "ts": ts.replace(hour=0, minute=0, second=0, microsecond=0),
                        "value": rank,
                        "meta": json.dumps({"source": "wayback"}),
                    }
                ],
            )
            stored += 1
        return f"wayback: {stored}/{len(snapshots)} снапшотов"
