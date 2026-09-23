"""Упоминания в СМИ (ф.10): GDELT — полная история с 2017, бесплатно.

Фильтр «не проплаченных площадок» — исключаем PR-wire домены прямо в
запросе GDELT (-domain:...). Свежие статьи складываем в mentions с флагом
is_pr для ленты на дашборде. CryptoPanic подключается бесплатным токеном
CRYPTOPANIC_TOKEN (env), опционально.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..models import Mention, Project
from . import register
from .base import Collector, upsert_metrics

log = logging.getLogger("collectors.mentions")

GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_START_YEAR = 2017  # глубже GDELT DOC не ищет
GDELT_RETRIES = 2  # вместо дефолтных 5: их 429 не проходит от повторов, только жжёт время
GDELT_GIVE_UP_AFTER = 4  # подряд неудачных запросов -> считаем GDELT недоступным на весь прогон

# Предохранитель: GDELT держит IP в кулдауне сутками, и полный перебор 10 лет × 9 монет
# × 5 попыток с backoff занимал >90 минут, не давая ни одной точки (23.09.2026 недельный
# прогон пришлось убивать руками; по расписанию он упёрся бы в лимит задачи в 3 часа).
# Сорвались подряд GDELT_GIVE_UP_AFTER запросов — пропускаем GDELT до конца процесса.
_gdelt_failures = 0
_gdelt_down = False


def gdelt_reset() -> None:
    """Сбросить предохранитель (тесты, повторный прогон в одном процессе)."""
    global _gdelt_failures, _gdelt_down
    _gdelt_failures, _gdelt_down = 0, False


def gdelt_is_down() -> bool:
    return _gdelt_down


def _gdelt_ok() -> None:
    global _gdelt_failures
    _gdelt_failures = 0


def _gdelt_failed() -> None:
    global _gdelt_failures, _gdelt_down
    _gdelt_failures += 1
    if _gdelt_failures >= GDELT_GIVE_UP_AFTER and not _gdelt_down:
        _gdelt_down = True
        log.error(
            "GDELT не отвечает %d запросов подряд — пропускаю его до конца прогона. "
            "Обычно это суточный кулдаун по IP: повторите `update --collector mentions` "
            "в другой день, интервал 6с в base.py не уменьшайте.",
            _gdelt_failures,
        )

PR_WIRE_DOMAINS = [
    "prnewswire.com", "globenewswire.com", "businesswire.com", "accesswire.com",
    "newsfilecorp.com", "einnews.com", "openpr.com", "abnewswire.com",
    "issuewire.com", "prunderground.com", "marketersmedia.com", "prweb.com",
]


def _query(keyword: str) -> str:
    exclusions = " ".join(f"-domain:{d}" for d in PR_WIRE_DOMAINS)
    kw = f'"{keyword}"' if " " in keyword else keyword
    return f"{kw} {exclusions} sourcelang:eng"


@register
class MentionsCollector(Collector):
    name = "mentions"
    scope = "project"
    description = "Упоминания в СМИ без PR-wire площадок, история с 2017 (ф.10)"

    def backfill(self, project: Project | None = None) -> str:
        assert project is not None
        keyword = project.trends_keyword or project.name
        year_now = datetime.now(timezone.utc).year
        if _gdelt_down:
            return "mentions: GDELT недоступен (кулдаун по IP), пропуск"

        rows = []
        for year in range(GDELT_START_YEAR, year_now + 1):
            try:
                data = self.http.get_json(
                    GDELT_DOC,
                    retries=GDELT_RETRIES,
                    params={
                        "query": _query(keyword),
                        "mode": "timelinevolraw",
                        "format": "json",
                        "startdatetime": f"{year}0101000000",
                        "enddatetime": f"{year}1231235959",
                    },
                )
                _gdelt_ok()
            except Exception as e:  # noqa: BLE001
                log.warning("GDELT %s %s: %s", keyword, year, e)
                _gdelt_failed()
                if _gdelt_down:
                    return "mentions: GDELT недоступен (кулдаун по IP), пропуск"
                continue
            for series in data.get("timeline", []):
                for point in series.get("data", []):
                    ts = datetime.strptime(point["date"], "%Y%m%dT%H%M%SZ")
                    rows.append(
                        {
                            "project_id": project.id,
                            "metric": "media_mentions",
                            "ts": ts,
                            "value": float(point.get("value") or 0),
                        }
                    )
        n = upsert_metrics(self.session, rows)

        articles = self._recent_articles(project, keyword)
        cp = self._cryptopanic(project)
        return f"mentions: {n} точек истории, {articles} статей, cryptopanic: {cp}"

    def _recent_articles(self, project: Project, keyword: str) -> int:
        """Свежие статьи (лента дашборда) — с пометкой PR-площадок."""
        if _gdelt_down:
            return 0
        try:
            data = self.http.get_json(
                GDELT_DOC,
                retries=GDELT_RETRIES,
                params={
                    "query": _query(keyword).replace(
                        " ".join(f"-domain:{d}" for d in PR_WIRE_DOMAINS), ""
                    ).strip(),
                    "mode": "artlist",
                    "maxrecords": "250",
                    "format": "json",
                    "timespan": "3months",
                },
            )
        except Exception as e:  # noqa: BLE001
            log.warning("GDELT artlist %s: %s", keyword, e)
            _gdelt_failed()
            return 0
        _gdelt_ok()
        rows = []
        for art in data.get("articles", []):
            url = art.get("url", "")
            if not url:
                continue
            domain = art.get("domain") or urlparse(url).netloc
            try:
                ts = datetime.strptime(art.get("seendate", ""), "%Y%m%dT%H%M%SZ")
            except ValueError:
                continue
            rows.append(
                {
                    "project_id": project.id,
                    "source": "gdelt",
                    "ts": ts,
                    "url": url[:1000],
                    "domain": domain,
                    "title": (art.get("title") or "")[:500],
                    "is_pr": any(domain.endswith(d) for d in PR_WIRE_DOMAINS),
                }
            )
        return self._upsert_mentions(rows)

    def _cryptopanic(self, project: Project) -> str:
        import os

        token = os.environ.get("CRYPTOPANIC_TOKEN", "")
        if not token:
            return "пропуск (нет CRYPTOPANIC_TOKEN)"
        try:
            data = self.http.get_json(
                "https://cryptopanic.com/api/v1/posts/",
                params={"auth_token": token, "currencies": project.symbol},
            )
        except Exception as e:  # noqa: BLE001
            return f"ошибка ({e})"
        rows = []
        for post in data.get("results", []):
            ts = datetime.fromisoformat(post["published_at"].replace("Z", "+00:00")).replace(tzinfo=None)
            source_domain = (post.get("source") or {}).get("domain", "")
            rows.append(
                {
                    "project_id": project.id,
                    "source": "cryptopanic",
                    "ts": ts,
                    "url": post.get("url", "")[:1000],
                    "domain": source_domain,
                    "title": (post.get("title") or "")[:500],
                    "is_pr": any(source_domain.endswith(d) for d in PR_WIRE_DOMAINS),
                }
            )
        return f"{self._upsert_mentions(rows)} постов"

    def _upsert_mentions(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        for chunk_start in range(0, len(rows), 300):
            chunk = rows[chunk_start : chunk_start + 300]
            stmt = sqlite_insert(Mention).values(chunk)
            stmt = stmt.on_conflict_do_nothing(index_elements=["project_id", "url"])
            self.session.execute(stmt)
        self.session.commit()
        return len(rows)
