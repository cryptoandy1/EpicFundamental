"""GitHub-активность (ф.5).

Полная история недельных коммитов через /stats/contributors (недельные
ряды по топ-100 контрибьюторам за всё время жизни репозитория) +
снапшот (stars, forks, issues) + месячная история релизов.

Без токена лимит GitHub — 60 запросов/час; с GITHUB_TOKEN (env) — 5000/час.
Эндпоинты /stats/* отвечают 202, пока GitHub считает статистику — ждём и
повторяем.
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

from ..models import Project
from . import register
from .base import Collector, upsert_metrics

log = logging.getLogger("collectors.github")

API = "https://api.github.com"


def _headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


@register
class GithubCollector(Collector):
    name = "github"
    scope = "project"
    description = "Недельные коммиты за всю историю + релизы + снапшот stars/forks (ф.5)"

    def _stats_contributors(self, repo: str) -> list[dict]:
        """GET со встроенным ожиданием 202 (статистика считается на стороне GitHub)."""
        url = f"{API}/repos/{repo}/stats/contributors"
        for attempt in range(6):
            resp = self.http.get(url, headers=_headers())
            if resp.status_code == 202:
                time.sleep(5)
                continue
            resp.raise_for_status()
            return resp.json() or []
        log.warning("%s: stats так и не посчитались (202)", repo)
        return []

    def backfill(self, project: Project | None = None) -> str:
        assert project is not None
        repos = self.repos_of(project)
        if not repos:
            return "нет репозиториев в конфиге"

        weekly: dict[datetime, float] = defaultdict(float)
        snapshot = {"stars": 0.0, "forks": 0.0, "open_issues": 0.0}
        releases_by_month: dict[datetime, float] = defaultdict(float)

        for repo in repos:
            try:
                for contributor in self._stats_contributors(repo):
                    for week in contributor.get("weeks", []):
                        ts = datetime.fromtimestamp(week["w"], tz=timezone.utc).replace(tzinfo=None)
                        weekly[ts] += week.get("c", 0)

                info = self.http.get_json(f"{API}/repos/{repo}", headers=_headers())
                snapshot["stars"] += info.get("stargazers_count", 0)
                snapshot["forks"] += info.get("forks_count", 0)
                snapshot["open_issues"] += info.get("open_issues_count", 0)

                page = 1
                while page <= 10:  # до 1000 релизов
                    rels = self.http.get_json(
                        f"{API}/repos/{repo}/releases",
                        params={"per_page": 100, "page": page},
                        headers=_headers(),
                    )
                    if not rels:
                        break
                    for rel in rels:
                        published = rel.get("published_at")
                        if not published:
                            continue
                        ts = datetime.fromisoformat(published.replace("Z", "+00:00"))
                        month = ts.replace(
                            day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None
                        )
                        releases_by_month[month] += 1
                    if len(rels) < 100:
                        break
                    page += 1
            except Exception as e:  # noqa: BLE001 — один сломанный репо не роняет прогон
                log.warning("github %s: %s", repo, e)

        rows = [
            {"project_id": project.id, "metric": "github_commits_week", "ts": ts, "value": v}
            for ts, v in weekly.items()
        ]
        rows += [
            {"project_id": project.id, "metric": "github_releases_month", "ts": ts, "value": v}
            for ts, v in releases_by_month.items()
        ]
        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=None
        )
        rows += [
            {"project_id": project.id, "metric": f"github_{k}", "ts": today, "value": v}
            for k, v in snapshot.items()
        ]
        n = upsert_metrics(self.session, rows)
        return f"github ({len(repos)} repo): {n} точек, commits weeks={len(weekly)}"
