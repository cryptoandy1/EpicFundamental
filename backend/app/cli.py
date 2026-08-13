"""CLI: python -m app <команда>

  sync              — синхронизировать projects.yaml в БД
  backfill          — выгрузить всю доступную историю (приоритет проекта)
  update            — инкрементально дособрать свежее
  screen            — прогнать скринер кандидатов (ф.3)
  ladder            — показать композитный скор «лесенки»
  list-collectors   — список коллекторов
  serve             — запустить FastAPI (uvicorn)
  export            — статический экспорт API в JSON (для GitHub Pages)
"""
from __future__ import annotations

import argparse
import logging
import sys

from .collectors import get_collectors
from .collectors.base import Http
from .config import sync_projects
from .db import SessionLocal, init_db
from .models import Project

log = logging.getLogger("cli")


def _run_collectors(mode: str, project_filter: str | None, collector_filter: list[str] | None) -> None:
    init_db()
    session = SessionLocal()
    sync_projects(session)
    http = Http()

    projects = session.query(Project).filter_by(approved=True).all()
    if project_filter:
        projects = [p for p in projects if p.id == project_filter]
        if not projects:
            print(f"Проект '{project_filter}' не найден в projects.yaml")
            sys.exit(1)

    for cls in get_collectors(collector_filter):
        collector = cls(session, http)
        method = collector.backfill if mode == "backfill" else collector.update
        if cls.scope == "market":
            if project_filter:
                continue  # рыночные коллекторы не зависят от проекта
            try:
                print(f"[{cls.name}] {method()}")
            except Exception as e:  # noqa: BLE001
                log.exception("%s", cls.name)
                print(f"[{cls.name}] ОШИБКА: {e}")
        else:
            for project in projects:
                try:
                    print(f"[{cls.name}] {project.id}: {method(project)}")
                except Exception as e:  # noqa: BLE001
                    log.exception("%s %s", cls.name, project.id)
                    print(f"[{cls.name}] {project.id}: ОШИБКА: {e}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(prog="python -m app", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sync", help="синхронизировать projects.yaml в БД")
    for name in ("backfill", "update"):
        p = sub.add_parser(name)
        p.add_argument("--project", help="только один проект (id из projects.yaml)")
        p.add_argument(
            "--collector",
            action="append",
            help="только указанные коллекторы (можно несколько раз)",
        )
    sub.add_parser("screen", help="скринер кандидатов (ф.3)")
    sub.add_parser("ladder", help="композитный скор «лесенки»")
    sub.add_parser("list-collectors")
    serve = sub.add_parser("serve", help="запустить API-сервер")
    serve.add_argument("--port", type=int, default=8000)
    export = sub.add_parser("export", help="статический экспорт API в JSON (для GitHub Pages)")
    export.add_argument("--out", help="каталог вывода (по умолчанию frontend/public/data)")

    args = parser.parse_args()

    if args.command == "sync":
        init_db()
        session = SessionLocal()
        projects = sync_projects(session)
        print(f"Синхронизировано проектов: {len(projects)}: " + ", ".join(p.id for p in projects))
    elif args.command in ("backfill", "update"):
        _run_collectors(args.command, args.project, args.collector)
    elif args.command == "screen":
        from .screener import run_screener

        init_db()
        session = SessionLocal()
        candidates = run_screener(session, Http())
        print(f"Кандидатов: {len(candidates)}")
        for c in candidates[:40]:
            print(f"  {c['symbol']:8} {c['name'][:30]:30} {c['reason']}")
        print("Утверждённые монеты переносите вручную в config/projects.yaml")
    elif args.command == "ladder":
        from .scoring import compute_ladder

        init_db()
        session = SessionLocal()
        sync_projects(session)
        for i, row in enumerate(compute_ladder(session), 1):
            score = f"{row['score']:.1f}" if row["score"] is not None else "нет данных"
            print(f"{i}. {row['symbol']:8} {row['name']:24} скор: {score}")
    elif args.command == "list-collectors":
        for cls in get_collectors():
            print(f"  {cls.name:14} [{cls.scope:7}] {cls.description}")
    elif args.command == "serve":
        import uvicorn

        uvicorn.run("app.main:app", port=args.port, reload=False)
    elif args.command == "export":
        from .export import DEFAULT_OUT, export_static

        files = export_static(args.out or DEFAULT_OUT)
        print(f"Экспортировано файлов: {len(files)} -> {args.out or DEFAULT_OUT}")


if __name__ == "__main__":
    main()
