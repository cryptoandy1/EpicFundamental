"""Предохранитель GDELT: не молотить вхолостую, когда их API держит IP в кулдауне.

23.09.2026 недельный прогон больше 90 минут перебирал 10 лет × 9 монет × 5 попыток,
не получив ни одной точки, и его пришлось убивать руками. По расписанию он упёрся бы
в лимит задачи (3 часа) и не дошёл бы до экспорта и публикации.
"""
from __future__ import annotations

import pytest

from app.collectors import mentions as m
from app.collectors.mentions import MentionsCollector


class DeadGdelt:
    """Любой запрос к GDELT падает; остальные URL не используются."""

    def __init__(self):
        self.calls = 0

    def get_json(self, url, **kwargs):
        self.calls += 1
        raise RuntimeError("Server error '429 Too Many Requests'")


@pytest.fixture(autouse=True)
def _reset_breaker():
    m.gdelt_reset()
    yield
    m.gdelt_reset()


def test_breaker_trips_and_stops_hammering(session, project):
    http = DeadGdelt()
    report = MentionsCollector(session, http).backfill(project)

    assert "GDELT недоступен" in report
    assert m.gdelt_is_down()
    # сработал на GDELT_GIVE_UP_AFTER, а не перебрал все 10 лет
    assert http.calls == m.GDELT_GIVE_UP_AFTER
    assert http.calls < 10, "перебор всех лет — это и была потеря часа"


def test_second_project_skipped_without_any_request(session, project):
    http = DeadGdelt()
    MentionsCollector(session, http).backfill(project)
    calls_after_first = http.calls

    report = MentionsCollector(session, http).backfill(project)
    assert "пропуск" in report
    assert http.calls == calls_after_first, "второй проект не должен трогать GDELT"


def test_success_resets_failure_streak(session, project):
    """Разовый сбой не должен копиться в предохранитель через весь прогон."""
    class Flaky:
        def __init__(self):
            self.n = 0

        def get_json(self, url, **kwargs):
            self.n += 1
            if self.n % 2 == 0:          # каждый второй запрос падает
                raise RuntimeError("timeout")
            return {"timeline": []}

    MentionsCollector(session, Flaky()).backfill(project)
    assert not m.gdelt_is_down(), "чередование успех/сбой не должно ронять предохранитель"
