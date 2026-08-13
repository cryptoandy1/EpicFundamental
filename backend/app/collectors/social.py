"""Соц-активность.

Ф.12 — Discord «по существу»: читаем JSON-экспорты DiscordChatExporter
(https://github.com/Tyrrrz/DiscordChatExporter) из project.discord_export_dir.
Двухступенчатая классификация:
  1) эвристика отсеивает мусор (gm/gn/hello/good project/эмодзи);
  2) остальное — классификация через Claude API батчами (ANTHROPIC_API_KEY),
     без ключа — только эвристика (грубее, но бесплатно).

Ф.6/ф.13 — Twitter без платного X API не автоматизируется: коллектор manual
читает config/manual_metrics.yaml (ручной ввод, напр. ceo_tweets_week) и
кладёт в общие метрики — слот на дашборде заполняется вашими данными.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from ..models import Project
from . import register
from .base import Collector, upsert_metrics

log = logging.getLogger("collectors.social")

CLAUDE_MODEL = os.environ.get("CLASSIFY_MODEL", "claude-opus-5")
BATCH_SIZE = 80  # сообщений на один запрос классификации

NOISE_RE = re.compile(
    r"^\s*(gm|gn|gmgm|hello+|hi+|hey+|wen\s+\w+|lfg+|wagmi|hodl|moon|to the moon|"
    r"good\s+(project|job|work)|nice\s+(project|one)|great\s+project|love\s+it|"
    r"\W*)\s*[!.❗\U0001F300-\U0001FAFF☀-➿]*\s*$",
    re.IGNORECASE,
)

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "substantive_indices": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Индексы сообщений, содержащих вопросы/обсуждение по существу проекта",
        }
    },
    "required": ["substantive_indices"],
    "additionalProperties": False,
}


def _heuristic_substantive(text: str) -> bool:
    """Грубая эвристика без LLM: не мусор, достаточно длинное или содержит вопрос."""
    if NOISE_RE.match(text or ""):
        return False
    words = len((text or "").split())
    return words >= 7 or ("?" in (text or "") and words >= 3)


def _classify_with_claude(texts: list[str]) -> set[int] | None:
    """Индексы содержательных сообщений; None — если Claude API недоступен."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        log.warning("пакет anthropic не установлен — используется эвристика")
        return None

    client = anthropic.Anthropic()
    substantive: set[int] = set()
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        numbered = "\n".join(f"{i}: {t[:300]}" for i, t in enumerate(batch))
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=2048,
                system=(
                    "Ты классифицируешь сообщения из Discord-сервера криптопроекта. "
                    "Содержательные — вопросы и обсуждения по существу проекта: "
                    "технология, токеномика, разработка, использование продукта, баги. "
                    "НЕ содержательные — приветствия (gm/gn/hello), пустой хайп "
                    "(good project, lfg, wen moon), спам, эмодзи, флуд."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": "Верни индексы содержательных сообщений:\n" + numbered,
                    }
                ],
                output_config={"format": {"type": "json_schema", "schema": CLASSIFY_SCHEMA}},
            )
            if response.stop_reason == "refusal":
                continue
            text = next(b.text for b in response.content if b.type == "text")
            for idx in json.loads(text)["substantive_indices"]:
                if 0 <= idx < len(batch):
                    substantive.add(start + idx)
        except Exception as e:  # noqa: BLE001 — не роняем прогон из-за одного батча
            log.warning("classify batch %s: %s", start, e)
            for i, t in enumerate(batch):  # деградация до эвристики
                if _heuristic_substantive(t):
                    substantive.add(start + i)
    return substantive


@register
class DiscordCollector(Collector):
    name = "discord"
    scope = "project"
    description = "Discord-активность по существу, без gm/gn (ф.12); LLM-классификация через Claude API"

    def backfill(self, project: Project | None = None) -> str:
        assert project is not None
        if not project.discord_export_dir:
            return "пропуск: discord_export_dir не задан (экспортируйте сервер DiscordChatExporter'ом)"
        export_dir = Path(project.discord_export_dir)
        if not export_dir.exists():
            return f"пропуск: папка {export_dir} не найдена"

        messages: list[tuple[datetime, str]] = []
        for file in export_dir.glob("*.json"):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                log.warning("%s: %s", file, e)
                continue
            for msg in data.get("messages", []):
                content = msg.get("content") or ""
                ts_raw = msg.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_raw).replace(tzinfo=None)
                except ValueError:
                    continue
                messages.append((ts, content))
        if not messages:
            return "в экспортах нет сообщений"

        candidates = [(ts, t) for ts, t in messages if not NOISE_RE.match(t)]
        texts = [t for _, t in candidates]
        llm_result = _classify_with_claude(texts)
        if llm_result is not None:
            substantive_flags = [i in llm_result for i in range(len(texts))]
            mode = f"Claude API ({CLAUDE_MODEL})"
        else:
            substantive_flags = [_heuristic_substantive(t) for t in texts]
            mode = "эвристика (нет ANTHROPIC_API_KEY)"

        weekly_total: dict[datetime, float] = defaultdict(float)
        weekly_substantive: dict[datetime, float] = defaultdict(float)
        for ts, _ in messages:
            week = (ts - timedelta(days=ts.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            weekly_total[week] += 1
        for (ts, _), flag in zip(candidates, substantive_flags):
            if flag:
                week = (ts - timedelta(days=ts.weekday())).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                weekly_substantive[week] += 1

        rows = [
            {"project_id": project.id, "metric": "discord_messages_week", "ts": ts, "value": v}
            for ts, v in weekly_total.items()
        ] + [
            {"project_id": project.id, "metric": "discord_substantive_week", "ts": ts, "value": v}
            for ts, v in weekly_substantive.items()
        ]
        n = upsert_metrics(self.session, rows)
        share = sum(substantive_flags) / len(messages) if messages else 0
        return f"discord: {len(messages)} сообщений, {share:.0%} по существу [{mode}], {n} точек"


@register
class ManualMetricsCollector(Collector):
    name = "manual"
    scope = "market"  # один прогон обрабатывает все проекты из файла
    description = "Ручной ввод метрик (ф.6, 13): config/manual_metrics.yaml — напр. ceo_tweets_week"

    def backfill(self, project: Project | None = None) -> str:
        path = Path(__file__).resolve().parents[3] / "config" / "manual_metrics.yaml"
        if not path.exists():
            return "пропуск: config/manual_metrics.yaml не найден"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rows = []
        for entry in data.get("entries", []) or []:
            for point in entry.get("points", []) or []:
                rows.append(
                    {
                        "project_id": entry["project"],
                        "metric": entry["metric"],
                        "ts": datetime.fromisoformat(str(point["date"])),
                        "value": float(point["value"]),
                    }
                )
        n = upsert_metrics(self.session, rows)
        return f"manual: {n} точек из manual_metrics.yaml"
