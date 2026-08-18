"""Реестр коллекторов. Каждый коллектор: backfill() — вся доступная история,
update() — инкрементальное обновление."""
from __future__ import annotations

from .base import Collector

_REGISTRY: dict[str, type[Collector]] = {}


def register(cls: type[Collector]) -> type[Collector]:
    _REGISTRY[cls.name] = cls
    return cls


def get_collectors(names: list[str] | None = None) -> list[type[Collector]]:
    # импорт здесь, чтобы регистрация сработала при первом обращении
    from . import (  # noqa: F401
        market,
        google_trends,
        funding,
        github_activity,
        github_ecosystem,
        unlocks,
        wallets,
        nodes,
        mentions,
        coinbase_app,
        social,
        defillama,
        nansen,
    )

    if names:
        missing = [n for n in names if n not in _REGISTRY]
        if missing:
            raise KeyError(f"Неизвестные коллекторы: {missing}. Есть: {sorted(_REGISTRY)}")
        return [_REGISTRY[n] for n in names]
    return list(_REGISTRY.values())
