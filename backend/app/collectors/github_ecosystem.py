"""GitHub-экосистема (ф.5): разработка НА ПЛАТФОРМЕ, а не от команды.

Метрика github_eco_new_repos_week — сколько новых репозиториев с топиком
экосистемы (topic:solana, topic:sui, ...) создано за неделю. Прокси «сколько
разработчиков приходит строить на платформе»: опережающий индикатор для
использования сети (TVL/комиссии) и цены. В лесенку идёт моментум 12 нед/24 нед
с порогом объёма базы (у малых экосистем счётчик редкий — см. scoring.py).
Текущая незавершённая неделя не пишется.

Источник — GitHub Search API (бесплатно, GITHUB_TOKEN → 30 req/min):
  GET /search/repositories?q=topic:{topic} created:{вс}..{сб}&per_page=1
  -> total_count. Форки поиск исключает по умолчанию. Топики проставляют с
  лагом, поэтому update() перезапрашивает последние UPDATE_WEEKS недель.

Топик задаётся в projects.yaml полем github_topic (по умолчанию = id проекта);
один топик на проект — OR между qualifiers у Search ненадёжен. Второй источник
на будущее — карта Electric Capital crypto-ecosystems (точнее, но дороже).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from ..config import load_config
from ..models import Project
from . import register
from .base import Collector, upsert_metrics
from .github_activity import API, week_start

log = logging.getLogger("collectors.github_eco")

BACKFILL_WEEKS = 104
UPDATE_WEEKS = 8
METRIC = "github_eco_new_repos_week"


def _headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def topic_of(project: Project) -> str:
    for p in load_config().get("projects", []):
        if p.get("id") == project.id:
            return str(p.get("github_topic") or project.id)
    return project.id


@register
class GithubEcosystemCollector(Collector):
    name = "github_eco"
    scope = "project"
    description = "Экосистема: новые репозитории с топиком проекта в неделю (GitHub Search) (ф.5)"

    def _count(self, topic: str, week: datetime) -> int:
        start = week.strftime("%Y-%m-%d")
        end = (week + timedelta(days=6)).strftime("%Y-%m-%d")
        data = self.http.get_json(
            f"{API}/search/repositories",
            params={"q": f"topic:{topic} created:{start}..{end}", "per_page": 1},
            headers=_headers(),
        )
        return int(data.get("total_count") or 0)

    def _collect(self, project: Project, weeks: int) -> str:
        topic = topic_of(project)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        last_complete = week_start(now) - timedelta(days=7)  # текущую незавершённую неделю не считаем
        rows = []
        failures = 0
        for i in range(weeks - 1, -1, -1):
            week = last_complete - timedelta(days=7 * i)
            try:
                value = self._count(topic, week)
            except Exception as e:  # noqa: BLE001 — одна неделя не роняет прогон
                failures += 1
                log.warning("github_eco %s %s: %s", topic, week.date(), e)
                continue
            rows.append(
                {
                    "project_id": project.id,
                    "metric": METRIC,
                    "ts": week,
                    "value": value,
                    "meta": json.dumps({"topic": topic}),
                }
            )
        n = upsert_metrics(self.session, rows)
        last = rows[-1]["value"] if rows else None
        report = f"github_eco topic:{topic}: {n} недель, последняя={last}"
        if failures:
            report += f", ошибок={failures}"
        return report

    def backfill(self, project: Project | None = None) -> str:
        assert project is not None
        return self._collect(project, BACKFILL_WEEKS)

    def update(self, project: Project | None = None) -> str:
        assert project is not None
        return self._collect(project, UPDATE_WEEKS)
