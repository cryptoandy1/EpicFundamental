"""Скринер кандидатов (ф.3): CoinGecko по категориям + фильтры из projects.yaml.

Результат — снапшот кандидатов в БД; финальный пул утверждаете вручную,
перенося монеты в config/projects.yaml.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .collectors.base import Http
from .config import load_config
from .models import ScreenerCandidate

log = logging.getLogger("screener")

COINGECKO_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"


def run_screener(session: Session, http: Http) -> list[dict]:
    cfg = (load_config().get("screener") or {})
    min_cap = cfg.get("min_market_cap", 100_000_000)
    max_cap = cfg.get("max_market_cap", 20_000_000_000)
    min_vol = cfg.get("min_volume_24h", 10_000_000)
    max_fdv_mc = cfg.get("max_fdv_mc_ratio", 3.0)
    categories = cfg.get("categories") or [None]

    seen: dict[str, dict] = {}
    for category in categories:
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 250,
            "page": 1,
            "sparkline": "false",
        }
        if category:
            params["category"] = category
        try:
            coins = http.get_json(COINGECKO_MARKETS, params=params)
        except Exception as e:  # noqa: BLE001
            log.warning("screener category=%s: %s", category, e)
            continue
        for coin in coins:
            cap = coin.get("market_cap") or 0
            vol = coin.get("total_volume") or 0
            fdv = coin.get("fully_diluted_valuation") or 0
            if not (min_cap <= cap <= max_cap) or vol < min_vol:
                continue
            fdv_mc = fdv / cap if cap and fdv else 0
            if fdv_mc and fdv_mc > max_fdv_mc:
                continue
            reasons = [
                f"cap ${cap / 1e9:.2f}B",
                f"vol ${vol / 1e6:.0f}M/сут",
                f"FDV/MC {fdv_mc:.2f}" if fdv_mc else "FDV/MC n/a",
            ]
            if category:
                reasons.append(f"категория {category}")
            entry = seen.setdefault(
                coin["id"],
                {
                    "coingecko_id": coin["id"],
                    "name": coin.get("name", ""),
                    "symbol": (coin.get("symbol") or "").upper(),
                    "market_cap": float(cap),
                    "fdv": float(fdv),
                    "volume_24h": float(vol),
                    "categories": category or "",
                    "reason": "; ".join(reasons),
                },
            )
            if category and category not in entry["categories"]:
                entry["categories"] = (entry["categories"] + "," + category).strip(",")

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    rows = [{"snapshot_date": today, **c} for c in seen.values()]
    for chunk_start in range(0, len(rows), 200):
        chunk = rows[chunk_start : chunk_start + 200]
        stmt = sqlite_insert(ScreenerCandidate).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["snapshot_date", "coingecko_id"],
            set_={
                "market_cap": stmt.excluded.market_cap,
                "fdv": stmt.excluded.fdv,
                "volume_24h": stmt.excluded.volume_24h,
                "categories": stmt.excluded.categories,
                "reason": stmt.excluded.reason,
            },
        )
        session.execute(stmt)
    session.commit()
    return sorted(seen.values(), key=lambda c: c["market_cap"], reverse=True)
