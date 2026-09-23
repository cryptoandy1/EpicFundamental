"""Скринер кандидатов (ф.3): CoinGecko по категориям + фильтры из projects.yaml.

Результат — снапшот кандидатов в БД; финальный пул утверждаете вручную,
перенося монеты в config/projects.yaml.

Watch-list (`screener.always_include`): монеты, которые попадают в снапшот всегда,
даже если не проходят фильтры — в `reason` перечислено, какие именно. Так лидеры,
выпавшие по капе или FDV/MC (HYPE, ZEC в 2026), не исчезают из поля зрения.
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
WATCHLIST = "watchlist"


def _filters(cfg: dict) -> dict:
    return {
        "min_cap": cfg.get("min_market_cap", 100_000_000),
        "max_cap": cfg.get("max_market_cap", 20_000_000_000),
        "min_vol": cfg.get("min_volume_24h", 10_000_000),
        "max_fdv_mc": cfg.get("max_fdv_mc_ratio", 3.0),
    }


def _candidate(coin: dict, category: str | None, f: dict) -> tuple[dict, list[str]]:
    """(запись кандидата, список непройденных фильтров — пустой, если прошёл все)."""
    cap = coin.get("market_cap") or 0
    vol = coin.get("total_volume") or 0
    fdv = coin.get("fully_diluted_valuation") or 0
    fdv_mc = fdv / cap if cap and fdv else 0

    failed: list[str] = []
    if cap < f["min_cap"]:
        failed.append(f"cap ${cap / 1e9:.2f}B < ${f['min_cap'] / 1e9:.1f}B")
    if cap > f["max_cap"]:
        failed.append(f"cap ${cap / 1e9:.2f}B > ${f['max_cap'] / 1e9:.0f}B")
    if vol < f["min_vol"]:
        failed.append(f"vol ${vol / 1e6:.0f}M < ${f['min_vol'] / 1e6:.0f}M")
    if fdv_mc and fdv_mc > f["max_fdv_mc"]:
        failed.append(f"FDV/MC {fdv_mc:.2f} > {f['max_fdv_mc']}")

    reasons = [
        f"cap ${cap / 1e9:.2f}B",
        f"vol ${vol / 1e6:.0f}M/сут",
        f"FDV/MC {fdv_mc:.2f}" if fdv_mc else "FDV/MC n/a",
    ]
    if category:
        reasons.append(f"категория {category}")
    entry = {
        "coingecko_id": coin["id"],
        "name": coin.get("name", ""),
        "symbol": (coin.get("symbol") or "").upper(),
        "market_cap": float(cap),
        "fdv": float(fdv),
        "volume_24h": float(vol),
        "categories": category or "",
        "reason": "; ".join(reasons),
    }
    return entry, failed


def run_screener(session: Session, http: Http) -> list[dict]:
    cfg = (load_config().get("screener") or {})
    f = _filters(cfg)
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
            entry, failed = _candidate(coin, category, f)
            if failed:
                continue
            existing = seen.setdefault(coin["id"], entry)
            if category and category not in existing["categories"]:
                existing["categories"] = (existing["categories"] + "," + category).strip(",")

    # watch-list: всегда в снапшоте, с перечнем непройденных фильтров
    watch = [w for w in (cfg.get("always_include") or []) if w not in seen]
    if watch:
        try:
            coins = http.get_json(
                COINGECKO_MARKETS,
                params={"vs_currency": "usd", "ids": ",".join(watch), "sparkline": "false"},
            )
        except Exception as e:  # noqa: BLE001
            log.warning("screener watch-list %s: %s", watch, e)
            coins = []
        for coin in coins:
            entry, failed = _candidate(coin, None, f)
            entry["categories"] = WATCHLIST
            verdict = "; ".join(failed) if failed else "проходит все фильтры"
            entry["reason"] = f"{WATCHLIST}; {verdict}; {entry['reason']}"
            seen[coin["id"]] = entry

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
