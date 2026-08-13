"""Автоэкспорт Discord-серверов пула для ф.12 (запускать из backend/).

    .venv\\Scripts\\python scripts\\discord_export.py [--days 120]

Требует:
- DISCORD_TOKEN в backend/.env — токен ВАШЕГО аккаунта Discord (формально
  экспорт юзер-токеном против ToS Discord; инструмент read-only, риск мал,
  но решение ваше);
- членство аккаунта в серверах (инвайты — discord_invite в projects.yaml);
- tools/DiscordChatExporter/DiscordChatExporter.Cli.exe (self-contained).

Для каждого проекта: резолвим invite -> guild, берём до MAX_CHANNELS текстовых
каналов с «содержательными» именами (general/chat/dev/...), экспортируем
последние N дней в JSON в discord_export_dir. Дальше:
    python -m app backfill --collector discord
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
CLI = ROOT / "tools" / "DiscordChatExporter" / "DiscordChatExporter.Cli.exe"
CONFIG = ROOT / "config" / "projects.yaml"

# каналы, где живёт осмысленное обсуждение (не announcements — там односторонний PR)
CHANNEL_RE = re.compile(r"general|chat|discussion|dev|builder|ecosystem|support|community", re.I)
MAX_CHANNELS = 3


def _token() -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv(BACKEND / ".env")
    except ImportError:
        pass
    return os.environ.get("DISCORD_TOKEN", "")


def _resolve_guild(client: httpx.Client, invite: str, token: str) -> tuple[str, str] | None:
    """(guild_id, name) по коду инвайта; пробуем с токеном и без."""
    for headers in ({"Authorization": token}, {}):
        try:
            r = client.get(f"https://discord.com/api/v10/invites/{invite}", headers=headers)
            if r.status_code == 200 and (g := r.json().get("guild")):
                return g["id"], g.get("name", "")
        except httpx.HTTPError:
            continue
    return None


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(CLI), *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def _list_channels(token: str, guild_id: str) -> list[tuple[str, str]]:
    """[(channel_id, name)] текстовых каналов гильдии."""
    proc = _run_cli("channels", "-t", token, "-g", guild_id)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    channels = []
    for line in proc.stdout.splitlines():
        # формат: "123456789 | Category / channel-name"
        m = re.match(r"^\s*(\d{15,21})\s*\|\s*(.+)$", line)
        if m:
            channels.append((m.group(1), m.group(2).strip()))
    return channels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=120, help="глубина экспорта (дней)")
    parser.add_argument("--project", help="только один проект (id)")
    args = parser.parse_args()

    token = _token()
    if not token:
        sys.exit("Нет DISCORD_TOKEN в backend/.env — добавьте строку DISCORD_TOKEN=...")
    if not CLI.exists():
        sys.exit(f"Не найден {CLI} — распакуйте DiscordChatExporter.Cli.win-x64.zip в tools/")

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    after = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")
    client = httpx.Client(timeout=20)

    for p in cfg.get("projects", []):
        pid, invite = p["id"], p.get("discord_invite")
        if args.project and pid != args.project:
            continue
        if not invite:
            continue
        resolved = _resolve_guild(client, invite, token)
        if not resolved:
            print(f"[{pid}] инвайт '{invite}' не резолвится — проверьте код в projects.yaml")
            continue
        guild_id, guild_name = resolved
        try:
            channels = _list_channels(token, guild_id)
        except RuntimeError as e:
            print(f"[{pid}] {guild_name}: нет доступа ({e}). Вступите: discord.gg/{invite}")
            continue

        picked = [c for c in channels if CHANNEL_RE.search(c[1])][:MAX_CHANNELS]
        if not picked:
            picked = channels[:MAX_CHANNELS]
        out_dir = BACKEND / (p.get("discord_export_dir") or f"data/discord/{pid}")
        out_dir.mkdir(parents=True, exist_ok=True)
        for ch_id, ch_name in picked:
            out = out_dir / f"{ch_id}.json"
            proc = _run_cli(
                "export", "-t", token, "-c", ch_id, "-f", "Json",
                "--after", after, "-o", str(out),
            )
            status = "ok" if proc.returncode == 0 else f"ошибка: {(proc.stderr or proc.stdout).strip()[:120]}"
            print(f"[{pid}] #{ch_name}: {status}")

    print("Готово. Дальше: python -m app backfill --collector discord")


if __name__ == "__main__":
    main()
