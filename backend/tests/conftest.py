"""Фикстуры: in-memory БД + фейковый HTTP с записанными ответами API."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Base, Project  # noqa: E402


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    yield s
    s.close()


@pytest.fixture()
def project(session):
    p = Project(
        id="testcoin",
        name="Testcoin",
        symbol="TST",
        chain="celestia",
        coingecko_id="testcoin",
        binance_symbol="TSTUSDT",
        github_repos='["org/repo"]',
        trends_keyword="testcoin",
    )
    session.add(p)
    session.commit()
    return p


class FakeHttp:
    """Подменяет Http: отдаёт заранее записанные ответы по подстроке URL."""

    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.calls: list[str] = []

    def _find(self, url: str, params: Any = None) -> Any:
        haystack = url + " " + str(params or "")
        self.calls.append(haystack)
        for needle, payload in self.responses.items():
            if needle in haystack:
                return payload
        raise AssertionError(f"нет фикстуры для {haystack}")

    def get_json(self, url: str, **kwargs) -> Any:
        return self._find(url, kwargs.get("params"))

    def post_json(self, url: str, payload: Any, **kwargs) -> Any:
        return self._find(url, payload)

    def get(self, url: str, **kwargs):  # pragma: no cover — для полноты интерфейса
        raise AssertionError("сырой get в тестах не используется")


@pytest.fixture()
def fake_http():
    return FakeHttp
