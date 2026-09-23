"""Регрессия на сбой 19.08–22.09.2026: под Планировщиком stdout — пайп в cp1252,
и print() кириллицы ронял каждый прогон. Здесь stdout/stderr подменяются на строгий
cp1252-поток: без _utf8_streams() тест падает с UnicodeEncodeError."""
from __future__ import annotations

import io
import sys

import pytest

from app import cli


def _cp1252_stream() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", write_through=True)


def _text(stream: io.TextIOWrapper) -> str:
    stream.flush()
    return stream.buffer.getvalue().decode("utf-8", errors="replace")


def test_cp1252_stream_rejects_cyrillic_without_fix():
    """Контроль самой обвязки: голый cp1252-поток действительно не принимает кириллицу."""
    with pytest.raises(UnicodeEncodeError):
        print("ОШИБКА", file=_cp1252_stream())


def test_list_collectors_under_cp1252(monkeypatch):
    out, err = _cp1252_stream(), _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setattr(sys, "argv", ["app", "list-collectors"])

    cli.main()  # печатает русские description всех коллекторов — без БД и сети

    text = _text(out)
    assert "Цена" in text
    assert "altseason" in text


def test_run_collectors_error_branch_under_cp1252(session, project, monkeypatch):
    """Ветка except в _run_collectors тоже печатает кириллицу («ОШИБКА») — именно она
    добивала процесс повторным исключением."""
    out, err = _cp1252_stream(), _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    cli._utf8_streams()

    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "SessionLocal", lambda: session)
    monkeypatch.setattr(cli, "sync_projects", lambda s: [])
    monkeypatch.setattr(cli, "Http", lambda: None)

    class Boom:
        name = "boom"
        scope = "project"
        description = "тест"

        def __init__(self, session, http):
            pass

        def update(self, project=None):
            raise RuntimeError("нет точек")

        backfill = update

    monkeypatch.setattr(cli, "get_collectors", lambda names=None: [Boom])

    cli._run_collectors("update", None, None)  # не должно бросить

    text = _text(out)
    assert "ОШИБКА" in text
    assert "нет точек" in text
