"""Базовый класс коллектора + HTTP-клиент с троттлингом и retry/backoff.

Бесплатные API капризны (CoinGecko ~30 req/min, Etherscan 5 req/s,
pytrends 429), поэтому:
- per-host минимальный интервал между запросами;
- экспоненциальный backoff на 429/5xx;
- бэкфилл большого пула может занять часы — это нормально.
"""
from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..models import Event, Metric, Project

log = logging.getLogger("collectors")

# Минимальный интервал между запросами к хосту, сек. Ключ — host или host+префикс
# пути (более длинный ключ побеждает): у GitHub Search свой лимит 30 req/min.
HOST_INTERVALS = {
    "api.coingecko.com": 2.5,
    "api.binance.com": 0.15,
    "api.github.com": 0.8,
    "api.github.com/search": 2.1,
    "api.nansen.ai": 0.1,  # Pro: 75 req/s — троттлинг символический
    "api.llama.fi": 0.5,
    "api.etherscan.io": 0.25,
    "api.gdeltproject.org": 6.0,  # GDELT жёстко режет частые запросы
    "cryptopanic.com": 1.5,
    "itunes.apple.com": 1.0,
    "trends.google.com": 5.0,
}
DEFAULT_INTERVAL = 0.5


class Http:
    """httpx.Client с per-host троттлингом и retry."""

    def __init__(self, timeout: float = 30.0):
        self.client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "EpicFundamental/0.1 (personal research tool)"},
            follow_redirects=True,
        )
        self._last_call: dict[str, float] = {}

    @staticmethod
    def _interval_key(url: str) -> tuple[str, float]:
        """(ключ троттлинга, интервал): самый длинный подходящий префикс host+path."""
        parsed = urlparse(url)
        full = parsed.netloc + parsed.path
        best_key, best_interval = parsed.netloc, HOST_INTERVALS.get(parsed.netloc, DEFAULT_INTERVAL)
        for key, interval in HOST_INTERVALS.items():
            if "/" in key and full.startswith(key) and len(key) > len(best_key):
                best_key, best_interval = key, interval
        return best_key, best_interval

    def _throttle(self, url: str) -> None:
        key, interval = self._interval_key(url)
        elapsed = time.monotonic() - self._last_call.get(key, 0.0)
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_call[key] = time.monotonic()

    @staticmethod
    def _rate_limit_wait(resp: httpx.Response) -> float | None:
        """GitHub сигналит исчерпание лимита 403/429 с X-RateLimit-Remaining: 0 —
        ждём до X-RateLimit-Reset (не дольше 90с)."""
        if resp.status_code not in (403, 429):
            return None
        if resp.headers.get("X-RateLimit-Remaining") != "0":
            return None
        try:
            reset = float(resp.headers.get("X-RateLimit-Reset", "0"))
        except ValueError:
            reset = 0.0
        return max(1.0, min(90.0, reset - time.time() + 1.0))

    def get(self, url: str, retries: int = 5, **kwargs) -> httpx.Response:
        backoff = 2.0
        for attempt in range(retries):
            self._throttle(url)
            try:
                resp = self.client.get(url, **kwargs)
            except httpx.HTTPError as e:
                if attempt == retries - 1:
                    raise
                log.warning("HTTP error %s (%s), retry in %.0fs", url, e, backoff)
                time.sleep(backoff)
                backoff *= 2
                continue
            limit_wait = self._rate_limit_wait(resp)
            if limit_wait is not None or resp.status_code in (429, 500, 502, 503, 504):
                if attempt == retries - 1:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After")
                wait = limit_wait or (float(retry_after) if retry_after else backoff)
                log.warning("%s -> %s, retry in %.0fs", url, resp.status_code, wait)
                time.sleep(wait)
                backoff *= 2
                continue
            return resp
        raise RuntimeError("unreachable")

    def get_json(self, url: str, **kwargs) -> Any:
        resp = self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def post(self, url: str, payload: Any, retries: int = 4, **kwargs) -> httpx.Response:
        """POST с тем же retry/backoff, что у get(); возвращает Response — нужен доступ
        к заголовкам (Nansen: X-Nansen-Credits-Remaining). 4xx (кроме 429/лимитов) не ретраим."""
        backoff = 2.0
        for attempt in range(retries):
            self._throttle(url)
            try:
                resp = self.client.post(url, json=payload, **kwargs)
            except httpx.HTTPError as e:
                if attempt == retries - 1:
                    raise
                log.warning("POST %s (%s), retry in %.0fs", url, e, backoff)
                time.sleep(backoff)
                backoff *= 2
                continue
            limit_wait = self._rate_limit_wait(resp)
            if limit_wait is not None or resp.status_code in (429, 500, 502, 503, 504):
                if attempt == retries - 1:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After")
                wait = limit_wait or (float(retry_after) if retry_after else backoff)
                log.warning("POST %s -> %s, retry in %.0fs", url, resp.status_code, wait)
                time.sleep(wait)
                backoff *= 2
                continue
            return resp
        raise RuntimeError("unreachable")

    def post_json(self, url: str, payload: Any, retries: int = 4, **kwargs) -> Any:
        """POST для JSON-RPC (ноды Solana/NEAR/Avalanche) и JSON-API."""
        resp = self.post(url, payload, retries=retries, **kwargs)
        resp.raise_for_status()
        return resp.json()


def upsert_metrics(session: Session, rows: Iterable[dict]) -> int:
    """rows: {project_id, metric, ts, value, meta?}. Идемпотентно (upsert)."""
    rows = [
        {
            "project_id": r.get("project_id", "_market"),
            "metric": r["metric"],
            "ts": r["ts"],
            "value": float(r["value"]),
            "meta": r.get("meta", ""),
        }
        for r in rows
    ]
    if not rows:
        return 0
    for chunk_start in range(0, len(rows), 500):
        chunk = rows[chunk_start : chunk_start + 500]
        stmt = sqlite_insert(Metric).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["project_id", "metric", "ts"],
            set_={"value": stmt.excluded.value, "meta": stmt.excluded.meta},
        )
        session.execute(stmt)
    session.commit()
    return len(rows)


def upsert_events(session: Session, rows: Iterable[dict]) -> int:
    """rows: {project_id, type, ts, title, value?, meta?}. Идемпотентно."""
    rows = [
        {
            "project_id": r["project_id"],
            "type": r["type"],
            "ts": r["ts"],
            "title": r.get("title", ""),
            "value": float(r.get("value", 0.0)),
            "meta": r.get("meta", ""),
        }
        for r in rows
    ]
    if not rows:
        return 0
    for chunk_start in range(0, len(rows), 500):
        chunk = rows[chunk_start : chunk_start + 500]
        stmt = sqlite_insert(Event).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["project_id", "type", "ts", "title"],
            set_={"value": stmt.excluded.value, "meta": stmt.excluded.meta},
        )
        session.execute(stmt)
    session.commit()
    return len(rows)


def last_metric_ts(session: Session, project_id: str, metric: str) -> datetime | None:
    row = (
        session.query(Metric.ts)
        .filter(Metric.project_id == project_id, Metric.metric == metric)
        .order_by(Metric.ts.desc())
        .first()
    )
    return row[0] if row else None


class Collector(ABC):
    """scope: "market" — один прогон на весь рынок; "project" — прогон на проект."""

    name: str = ""
    scope: str = "project"  # market | project
    description: str = ""

    def __init__(self, session: Session, http: Http):
        self.session = session
        self.http = http

    @abstractmethod
    def backfill(self, project: Project | None = None) -> str:
        """Выгрузить всю доступную историю. Возвращает краткий отчёт."""

    def update(self, project: Project | None = None) -> str:
        """Инкрементально дособрать свежее. По умолчанию — как backfill
        (все коллекторы идемпотентны через upsert)."""
        return self.backfill(project)

    @staticmethod
    def repos_of(project: Project) -> list[str]:
        return json.loads(project.github_repos) if project.github_repos else []
