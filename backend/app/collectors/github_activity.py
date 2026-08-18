"""GitHub-активность ядра протокола (ф.5): разработка ОТ КОМАНДЫ.

Метрики (недельная сетка GitHub — воскресенье 00:00 UTC; текущая незавершённая
неделя не пишется, чтобы не занижать моментум):
  github_commits_week      — коммиты по репо ядра (без ботов);
  github_active_devs_week  — уникальные разработчики с >=1 коммитом за неделю
                             (дедуп между репо проекта). Именно этот ряд идёт
                             в лесенку как моментум 28д/84д — устойчив к монорепо
                             и стилю коммитов, отражает «сколько людей реально
                             пишут код», а не масштаб репозитория.
Плюс снапшот (stars, forks, issues) и месячная история релизов.

Источники недельных рядов, по убыванию предпочтения:
  1) /stats/contributors — вся история репо, по каждому контрибьютору;
     202 = GitHub ещё считает, ждём и повторяем;
  2) fallback, если stats вернула пустой список (напр. anza-xyz/agave):
     листинг /repos/{repo}/commits за последние FALLBACK_WEEKS недель.
Если репо не дал данных ни одним путём — недельные ряды проекта НЕ пишутся
(иначе нули/пропуски перетёрли бы ранее собранные значения).

Без токена лимит GitHub — 60 запросов/час; с GITHUB_TOKEN (env) — 5000/час.
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ..models import Project
from . import register
from .base import Collector, upsert_metrics

log = logging.getLogger("collectors.github")

API = "https://api.github.com"
FALLBACK_WEEKS = 104  # глубина листинга коммитов, если stats недоступна
FALLBACK_MAX_PAGES = 200  # x100 коммитов


def _headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _is_bot(author: dict | None, login: str) -> bool:
    return bool(author and author.get("type") == "Bot") or login.endswith("[bot]")


def week_start(ts: datetime) -> datetime:
    """Начало недели по сетке GitHub stats: воскресенье 00:00 UTC (naive)."""
    ts = ts.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    return ts - timedelta(days=(ts.weekday() + 1) % 7)


class WeeklyDevs:
    """Аккумулятор по проекту: неделя -> коммиты и неделя -> набор логинов."""

    def __init__(self) -> None:
        self.commits: dict[datetime, float] = defaultdict(float)
        self.logins: dict[datetime, set[str]] = defaultdict(set)
        self.covered: set[datetime] = set()  # недели, за которые данные есть (в т.ч. нулевые)

    def add(self, week: datetime, login: str, commits: int) -> None:
        self.covered.add(week)
        if commits <= 0:
            return
        self.commits[week] += commits
        self.logins[week].add(login)

    def cover(self, start: datetime, end: datetime) -> None:
        week = week_start(start)
        while week <= end:
            self.covered.add(week)
            week += timedelta(days=7)


@register
class GithubCollector(Collector):
    name = "github"
    scope = "project"
    description = "Ядро: активные разработчики + коммиты в неделю, релизы, stars/forks (ф.5)"

    # --- источники недельных рядов ---

    def _stats_contributors(self, repo: str) -> list[dict]:
        """GET со встроенным ожиданием 202 (статистика считается на стороне GitHub)."""
        url = f"{API}/repos/{repo}/stats/contributors"
        for _attempt in range(6):
            resp = self.http.get(url, headers=_headers())
            if resp.status_code == 202:
                time.sleep(5)
                continue
            resp.raise_for_status()
            return resp.json() or []
        log.warning("%s: stats так и не посчитались (202)", repo)
        return []

    def _from_stats(self, repo: str, acc: WeeklyDevs) -> bool:
        """True, если stats дала хоть какие-то данные."""
        contributors = self._stats_contributors(repo)
        if not contributors:
            return False
        for contributor in contributors:
            author = contributor.get("author") or {}
            login = author.get("login") or ""
            if not login or _is_bot(author, login):
                continue
            for week in contributor.get("weeks", []):
                ts = datetime.fromtimestamp(week["w"], tz=timezone.utc).replace(tzinfo=None)
                acc.add(ts, login, int(week.get("c", 0) or 0))
        return True

    def _from_commits(self, repo: str, acc: WeeklyDevs, weeks: int = FALLBACK_WEEKS) -> bool:
        """Fallback: листинг коммитов за последние `weeks` недель (автор -> неделя)."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        since_dt = now - timedelta(weeks=weeks)
        since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        first_week = week_start(since_dt)
        seen_any = False
        page = 1
        while page <= FALLBACK_MAX_PAGES:
            commits = self.http.get_json(
                f"{API}/repos/{repo}/commits",
                params={"since": since, "per_page": 100, "page": page},
                headers=_headers(),
            )
            if not commits:
                break
            for c in commits:
                author = c.get("author") or {}
                meta = (c.get("commit") or {}).get("author") or {}
                login = author.get("login") or meta.get("email") or ""
                if not login or _is_bot(author, login):
                    continue
                date = meta.get("date")
                if not date:
                    continue
                ts = datetime.fromisoformat(date.replace("Z", "+00:00"))
                week = week_start(ts)
                if week < first_week:
                    continue  # rebase: авторская дата старше окна — не трогаем недели вне покрытия
                acc.add(week, login, 1)
                seen_any = True
            if len(commits) < 100:
                break
            page += 1
        if seen_any:
            acc.cover(since_dt, now)
        return seen_any

    # --- основной прогон ---

    def backfill(self, project: Project | None = None) -> str:
        assert project is not None
        repos = self.repos_of(project)
        if not repos:
            return "нет репозиториев в конфиге"

        acc = WeeklyDevs()
        snapshot = {"stars": 0.0, "forks": 0.0, "open_issues": 0.0}
        releases_by_month: dict[datetime, float] = defaultdict(float)
        notes: list[str] = []
        weekly_ok = True

        for repo in repos:
            try:
                if not self._from_stats(repo, acc):
                    if self._from_commits(repo, acc):
                        notes.append(f"{repo}: stats пуста → commits API, {FALLBACK_WEEKS} недель")
                    else:
                        weekly_ok = False
                        notes.append(f"{repo}: НЕТ данных ни stats, ни commits")
            except Exception as e:  # noqa: BLE001 — один сломанный репо не роняет прогон
                weekly_ok = False
                log.warning("github %s: %s", repo, e)
                notes.append(f"{repo}: ошибка {e}")

            try:
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
            except Exception as e:  # noqa: BLE001
                log.warning("github %s (snapshot/releases): %s", repo, e)

        rows: list[dict] = []
        # незавершённую текущую неделю не пишем — иначе она занижает моментум у всех монет
        current_week = week_start(datetime.now(timezone.utc).replace(tzinfo=None))
        if weekly_ok and acc.covered:
            for ts in sorted(acc.covered):
                if ts >= current_week:
                    continue
                rows.append(
                    {"project_id": project.id, "metric": "github_commits_week", "ts": ts, "value": acc.commits.get(ts, 0.0)}
                )
                rows.append(
                    {
                        "project_id": project.id,
                        "metric": "github_active_devs_week",
                        "ts": ts,
                        "value": len(acc.logins.get(ts, ())),
                    }
                )
        elif not weekly_ok:
            notes.append("недельные ряды НЕ записаны (сбой репо — чтобы не перетереть данные)")

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
        report = f"github ({len(repos)} repo): {n} точек, недель={len(acc.covered)}"
        if notes:
            report += "; " + "; ".join(notes)
        return report
