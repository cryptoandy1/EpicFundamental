"""Истёкший GITHUB_TOKEN не должен молча съедать весь прогон.

23.09.2026 классический ghp_-токен истёк, и коллектор отрапортовал «3 точек, недель=0»
по всем девяти проектам: каждый запрос сгорал на 401, данных не появилось, прогон
считался успешным. Теперь токен проверяется один раз и при 401 снимается.
"""
from __future__ import annotations

import pytest

from app.collectors import github_auth
from app.collectors.github_auth import github_headers, reset_token_check


class Http401:
    """Отдаёт 401 на любой запрос и считает обращения."""

    def __init__(self, error: Exception | None = None):
        self.calls = 0
        self.error = error or RuntimeError("Client error '401 Unauthorized' for url ...")

    def get_json(self, url, **kwargs):
        self.calls += 1
        raise self.error


class HttpOk:
    def __init__(self):
        self.calls = 0

    def get_json(self, url, **kwargs):
        self.calls += 1
        return {"resources": {"core": {"remaining": 4999, "limit": 5000}}}


@pytest.fixture(autouse=True)
def _clean_token_cache(monkeypatch):
    reset_token_check()
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    yield
    reset_token_check()


def test_no_token_means_plain_accept_header():
    assert github_headers(HttpOk()) == github_auth.ACCEPT


def test_valid_token_is_used_and_checked_once(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_valid")
    http = HttpOk()
    for _ in range(4):
        assert github_headers(http)["Authorization"] == "Bearer ghp_valid"
    assert http.calls == 1, "проверка токена должна быть одна на процесс"


def test_expired_token_is_dropped_and_not_rechecked(monkeypatch, caplog):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_expired")
    http = Http401()
    with caplog.at_level("ERROR"):
        headers = github_headers(http)
    assert "Authorization" not in headers, "битый токен должен быть снят"
    assert "недействителен" in caplog.text
    assert "github.com/settings/tokens" in caplog.text, "в логе должно быть, что делать"

    for _ in range(3):
        assert "Authorization" not in github_headers(http)
    assert http.calls == 1, "не долбим GitHub повторными проверками"


def test_network_error_keeps_token(monkeypatch):
    """«GitHub недоступен» — не повод выбрасывать рабочий токен."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_valid")
    http = Http401(error=RuntimeError("Connection timed out"))
    assert github_headers(http)["Authorization"] == "Bearer ghp_valid"


def test_changed_token_is_rechecked(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_expired")
    http401 = Http401()
    assert "Authorization" not in github_headers(http401)

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_new")   # пользователь выпустил новый
    http_ok = HttpOk()
    assert github_headers(http_ok)["Authorization"] == "Bearer ghp_new"
    assert http_ok.calls == 1
