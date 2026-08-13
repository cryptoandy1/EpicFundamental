"""Google Trends через pytrends (ф.1 — BTC «пик = сливаем», ф.14 — по проекту).

Бэкфилл: timeframe="all" — месячные точки за всю историю,
"today 5-y" — недельные за 5 лет. Google охотно отдаёт 429 —
между запросами длинная пауза, ошибки не роняют прогон.
"""
from __future__ import annotations

import logging
import time

from ..models import MARKET, Project
from . import register
from .base import Collector, upsert_metrics

log = logging.getLogger("collectors.trends")

PAUSE_BETWEEN_CALLS = 8  # сек; Google банит частые запросы


def _fetch_keyword(keyword: str) -> dict[str, list]:
    """{timeframe_metric: [(ts, value), ...]} для одного запроса."""
    from pytrends.request import TrendReq  # ленивый импорт: тянет pandas

    # ВАЖНО: retries= в TrendReq не передаём — pytrends ломается на urllib3 2.x
    # (method_whitelist); ретраим сами.
    pytrends = TrendReq(hl="en-US", tz=0)
    out: dict[str, list] = {}
    for timeframe, metric in (("all", "trends_monthly"), ("today 5-y", "trends_weekly")):
        out[metric] = []
        for attempt in range(3):
            try:
                pytrends.build_payload([keyword], timeframe=timeframe)
                df = pytrends.interest_over_time()
                if not df.empty:
                    if "isPartial" in df.columns:
                        df = df[~df["isPartial"].astype(bool)]
                    out[metric] = [
                        (ts.to_pydatetime().replace(tzinfo=None), float(v))
                        for ts, v in df[keyword].items()
                    ]
                break
            except Exception as e:  # noqa: BLE001 — Google 429 и капризы не роняют прогон
                log.warning("pytrends '%s' %s (попытка %d): %s", keyword, timeframe, attempt + 1, e)
                time.sleep(PAUSE_BETWEEN_CALLS * (attempt + 2))
        time.sleep(PAUSE_BETWEEN_CALLS)
    return out


class _TrendsBase(Collector):
    def _collect(self, keyword: str, project_id: str) -> str:
        series = _fetch_keyword(keyword)
        parts = []
        for metric, points in series.items():
            n = upsert_metrics(
                self.session,
                [
                    {"project_id": project_id, "metric": metric, "ts": ts, "value": v}
                    for ts, v in points
                ],
            )
            parts.append(f"{metric}: {n}")
        return f"'{keyword}' -> " + ", ".join(parts)


@register
class TrendsCollector(_TrendsBase):
    name = "trends"
    scope = "project"
    description = "Google Trends по проекту (ф.14): месячные за всю историю + недельные за 5 лет"

    def backfill(self, project: Project | None = None) -> str:
        assert project is not None
        keyword = project.trends_keyword or project.name
        return self._collect(keyword, project.id)


@register
class BtcTrendsCollector(_TrendsBase):
    name = "btc_trends"
    scope = "market"
    description = "Google Trends по 'bitcoin' (ф.1): пик интереса = сигнал слива"

    def backfill(self, project: Project | None = None) -> str:
        return self._collect("bitcoin", MARKET)
