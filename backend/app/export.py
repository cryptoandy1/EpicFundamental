"""Статический экспорт API в JSON-файлы для GitHub Pages.

Повторяет ответы всех эндпоинтов main.py в файлы:
  data/market/overview.json, data/projects.json, data/projects/{id}.json,
  data/screener.json, data/ladder.json
Фронтенд в статическом режиме (NEXT_PUBLIC_STATIC=1) читает эти файлы
вместо живого API.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import sync_projects
from .db import SessionLocal, init_db
from .main import ladder, list_projects, market_overview, project_detail, screener_candidates
from .models import Project

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "data"


def export_static(out_dir: Path | str = DEFAULT_OUT) -> list[Path]:
    """Пишет JSON-снапшоты всех эндпоинтов; возвращает список файлов."""
    out = Path(out_dir)
    init_db()
    session = SessionLocal()
    try:
        sync_projects(session)
        project_ids = [p.id for p in session.query(Project).filter_by(approved=True).all()]
    finally:
        session.close()

    payloads: dict[str, object] = {
        "market/overview.json": market_overview(),
        "projects.json": list_projects(),
        "screener.json": screener_candidates(),
        "ladder.json": ladder(),
    }
    for pid in project_ids:
        payloads[f"projects/{pid}.json"] = project_detail(pid)

    written = []
    for rel, payload in payloads.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        written.append(path)
    return written
