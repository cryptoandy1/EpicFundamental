"""Ворота входа в лесенку: сезон биткоина или ротация в альты (бесплатный CoinGecko).

Лесенка отвечает «в какую монету входить», но не «когда начинать». Исторически альты
догоняют BTC после его новых максимумов, когда доминация BTC разворачивается вниз.
Две метрики под project_id "_market":

- btc_dominance_pct           — доля BTC в капитализации рынка (/global);
- alts_beating_btc_30d_pct    — доля топ-100 монет (без стейблкоинов и обёрток), обогнавших
                                BTC за 30 дней (/coins/markets, price_change_percentage=30d).
                                Аналог Altcoin Season Index (там 90 дней — у бесплатного
                                CoinGecko такого окна нет, 30 дней — прокси).

Снапшот на сегодня, истории задним числом нет — копится с первого запуска. Ворота
(main.market_overview) открыты, когда ≥50% альтов обгоняют BTC И доминация ниже,
чем 4 недели назад.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..models import MARKET, Project
from . import register
from .base import Collector, upsert_metrics

log = logging.getLogger("collectors.altseason")

COINGECKO = "https://api.coingecko.com/api/v3"
ALTS_THRESHOLD_PCT = 50.0

# Стейблкоины и обёртки/стейкинг-деривативы BTC/ETH/SOL/BNB — не «альты» по смыслу индекса.
EXCLUDED_IDS = {
    "tether", "usd-coin", "dai", "binance-usd", "first-digital-usd", "ethena-usde", "usds",
    "paypal-usd", "true-usd", "frax", "usd1-world-liberty-financial", "usdd", "ethena-staked-usde",
    "wrapped-bitcoin", "coinbase-wrapped-btc", "lombard-staked-btc", "tbtc", "binance-bitcoin",
    "weth", "wrapped-steth", "staked-ether", "wrapped-eeth", "rocket-pool-eth", "wrapped-beacon-eth",
    "binance-staked-sol", "jito-staked-sol", "msol", "wrapped-bnb", "leo-token",
}
WRAPPED_SYMBOLS = ("wbtc", "cbbtc", "lbtc", "tbtc", "weth", "steth", "wsteth", "weeth", "reth",
                   "bnsol", "jitosol", "msol", "wbnb", "ezeth", "rseth")


def _is_alt(coin: dict) -> bool:
    cid = coin.get("id", "")
    symbol = (coin.get("symbol") or "").lower()
    if cid == "bitcoin" or cid in EXCLUDED_IDS or symbol in WRAPPED_SYMBOLS:
        return False
    price = coin.get("current_price") or 0
    change = coin.get("price_change_percentage_30d_in_currency")
    # стейблкоин, не попавший в список: цена ≈ $1 и почти не двигается
    if abs(price - 1.0) < 0.02 and change is not None and abs(change) < 2:
        return False
    return change is not None


def alts_beating_btc_share(markets: list[dict]) -> tuple[float | None, int, int]:
    """(доля альтов, обогнавших BTC за 30д, %; сколько обогнали; сколько альтов всего)."""
    btc = next((c for c in markets if c.get("id") == "bitcoin"), None)
    btc_change = btc.get("price_change_percentage_30d_in_currency") if btc else None
    if btc_change is None:
        return None, 0, 0
    alts = [c for c in markets if _is_alt(c)]
    if not alts:
        return None, 0, 0
    beating = sum(1 for c in alts if c["price_change_percentage_30d_in_currency"] > btc_change)
    return round(beating / len(alts) * 100, 1), beating, len(alts)


@register
class AltseasonCollector(Collector):
    name = "altseason"
    scope = "market"
    description = "Ворота входа: доминация BTC + доля топ-100, обгоняющих BTC за 30д (CoinGecko)"

    def backfill(self, project: Project | None = None) -> str:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        parts: list[str] = []

        try:
            data = (self.http.get_json(f"{COINGECKO}/global") or {}).get("data") or {}
            dominance = (data.get("market_cap_percentage") or {}).get("btc")
            if dominance is None:
                raise ValueError("нет market_cap_percentage.btc")
            upsert_metrics(
                self.session,
                [{"project_id": MARKET, "metric": "btc_dominance_pct", "ts": today, "value": dominance}],
            )
            parts.append(f"доминация BTC {dominance:.1f}%")
        except Exception as e:  # noqa: BLE001 — вторая метрика независима
            log.warning("CoinGecko /global: %s", e)
            parts.append(f"доминация: ошибка ({e})")

        try:
            markets = self.http.get_json(
                f"{COINGECKO}/coins/markets",
                params={
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": 100,
                    "page": 1,
                    "sparkline": "false",
                    "price_change_percentage": "30d",
                },
            )
            share, beating, total = alts_beating_btc_share(markets)
            if share is None:
                raise ValueError("нет 30-дневных изменений в ответе")
            upsert_metrics(
                self.session,
                [
                    {
                        "project_id": MARKET,
                        "metric": "alts_beating_btc_30d_pct",
                        "ts": today,
                        "value": share,
                        "meta": json.dumps({"beating": beating, "alts": total}),
                    }
                ],
            )
            parts.append(f"альтов обгоняют BTC за 30д: {beating}/{total} = {share:.0f}%")
        except Exception as e:  # noqa: BLE001
            log.warning("CoinGecko /coins/markets: %s", e)
            parts.append(f"альты vs BTC: ошибка ({e})")

        return "; ".join(parts)
