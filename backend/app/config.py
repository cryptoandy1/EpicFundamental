"""Загрузка config/projects.yaml и синхронизация в БД."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from .models import Project, Wallet

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "projects.yaml"


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sync_projects(session: Session) -> list[Project]:
    """projects.yaml — источник истины; создаёт/обновляет проекты и кошельки."""
    cfg = load_config()
    projects: list[Project] = []
    for p in cfg.get("projects", []):
        proj = session.get(Project, p["id"])
        if proj is None:
            proj = Project(id=p["id"])
            session.add(proj)
        proj.name = p.get("name", p["id"])
        proj.symbol = p.get("symbol", "").upper()
        proj.chain = p.get("chain", "")
        proj.coingecko_id = p.get("coingecko_id", "")
        proj.binance_symbol = p.get("binance_symbol", "")
        repos = p.get("github", [])
        proj.github_repos = json.dumps(repos)
        proj.github_org = repos[0].split("/")[0] if repos else ""
        proj.twitter_project = p.get("twitter_project", "")
        proj.twitter_ceo = p.get("twitter_ceo", "")
        proj.discord_export_dir = p.get("discord_export_dir", "")
        proj.token_contracts = json.dumps(p.get("token_contracts", {}) or {})
        proj.trends_keyword = p.get("trends_keyword", p.get("name", p["id"]))
        proj.approved = bool(p.get("approved", True))
        proj.notes = p.get("notes", "")

        for w in p.get("wallets", []) or []:
            existing = (
                session.query(Wallet)
                .filter_by(chain=w["chain"], address=w["address"])
                .one_or_none()
            )
            if existing is None:
                session.add(
                    Wallet(
                        project_id=proj.id,
                        chain=w["chain"],
                        address=w["address"],
                        label=w.get("label", "team"),
                        name=w.get("name", ""),
                    )
                )
        projects.append(proj)
    session.commit()
    return projects
