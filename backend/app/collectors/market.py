"""Цена/капитализация/объём.

- Binance klines: ПОЛНАЯ история дневной цены с листинга (бесплатно, без ключа).
- CoinGecko market_chart: капа и объём (бесплатный тариф отдаёт максимум 365 дней).
- Genesis-дата и категории — из CoinGecko /coins/{id} (ф.4, возраст проекта).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..models import MARKET, Project
from . import register
from .base import Collector, Http, last_metric_ts, upsert_metrics

log = logging.getLogger("collectors.market")

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
COINGECKO = "https://api.coingecko.com/api/v3"


def fetch_binance_daily(
    http: Http, symbol: str, start_ms: int = 0
) -> list[tuple[datetime, float, float]]:
    """(дата, close, quote_volume) по дневным свечам с листинга."""
    out: list[tuple[datetime, float, float]] = []
    cursor = start_ms
    while True:
        data = http.get_json(
            BINANCE_KLINES,
            params={"symbol": symbol, "interval": "1d", "limit": 1000, "startTime": cursor},
        )
        if not data:
            break
        for k in data:
            ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).replace(tzinfo=None)
            out.append((ts, float(k[4]), float(k[7])))
        if len(data) < 1000:
            break
        cursor = data[-1][6] + 1  # closeTime + 1ms
    return out


@register
class MarketCollector(Collector):
    name = "market"
    scope = "project"
    description = "Цена (Binance, вся история) + капа/объём (CoinGecko, 365д) + genesis-дата"

    def backfill(self, project: Project | None = None) -> str:
        assert project is not None
        parts: list[str] = []

        if project.binance_symbol:
            last = last_metric_ts(self.session, project.id, "price_usd")
            start_ms = int(last.timestamp() * 1000) if last else 0
            candles = fetch_binance_daily(self.http, project.binance_symbol, start_ms)
            n = upsert_metrics(
                self.session,
                [
                    {"project_id": project.id, "metric": "price_usd", "ts": ts, "value": close}
                    for ts, close, _ in candles
                ],
            )
            parts.append(f"price_usd: {n} точек")

        if project.coingecko_id:
            try:
                chart = self.http.get_json(
                    f"{COINGECKO}/coins/{project.coingecko_id}/market_chart",
                    params={"vs_currency": "usd", "days": 365},
                )
                rows = []
                for key, metric in (("market_caps", "market_cap"), ("total_volumes", "volume_24h")):
                    for ms, value in chart.get(key, []):
                        ts = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
                        ts = ts.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
                        rows.append(
                            {"project_id": project.id, "metric": metric, "ts": ts, "value": value}
                        )
                n = upsert_metrics(self.session, rows)
                parts.append(f"cap/volume: {n} точек")
            except Exception as e:  # noqa: BLE001 — не роняем весь бэкфилл
                log.warning("CoinGecko market_chart %s: %s", project.coingecko_id, e)
                parts.append(f"cap/volume: ошибка ({e})")

            if project.genesis_date is None:
                try:
                    info = self.http.get_json(
                        f"{COINGECKO}/coins/{project.coingecko_id}",
                        params={
                            "localization": "false",
                            "tickers": "false",
                            "market_data": "false",
                            "community_data": "false",
                            "developer_data": "false",
                        },
                    )
                    gd = info.get("genesis_date")
                    meta = {"categories": info.get("categories", [])}
                    if gd:
                        project.genesis_date = datetime.fromisoformat(gd)
                    if not project.notes:
                        project.notes = json.dumps(meta, ensure_ascii=False)
                    self.session.commit()
                    parts.append(f"genesis: {gd}")
                except Exception as e:  # noqa: BLE001
                    log.warning("CoinGecko /coins %s: %s", project.coingecko_id, e)
        return "; ".join(parts) or "нет источников (binance_symbol/coingecko_id пусты)"


@register
class BtcMarketCollector(Collector):
    name = "btc"
    scope = "market"
    description = "BTC: вся история дневной цены (для «Обзора рынка»)"

    def backfill(self, project: Project | None = None) -> str:
        last = last_metric_ts(self.session, MARKET, "btc_price_usd")
        start_ms = int(last.timestamp() * 1000) if last else 0
        candles = fetch_binance_daily(self.http, "BTCUSDT", start_ms)
        n = upsert_metrics(
            self.session,
            [
                {"project_id": MARKET, "metric": "btc_price_usd", "ts": ts, "value": close}
                for ts, close, _ in candles
            ],
        )
        return f"btc_price_usd: {n} точек"
