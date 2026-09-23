"""Единая авторизация GitHub для коллекторов ядра (ф.5) и экосистемы.

Токен проверяется ОДИН раз за процесс: если GitHub отвечает 401, заголовок снимается
и дальше работаем анонимно (60 запросов/час вместо 5000). Без этого каждый запрос
сгорал бы на 401, и недельный прогон «успешно» собирал бы ноль точек — так и случилось
23.09.2026, когда истёк классический ghp_-токен: коллектор отрапортовал «3 точек,
недель=0» по всем девяти проектам, а факторы github_core_devs и github_ecosystem
остались пустыми.

Сетевая ошибка при проверке токен НЕ выбрасывает: отличаем «токен плохой» от
«GitHub сейчас недоступен».
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("collectors.github")

API = "https://api.github.com"
ACCEPT = {"Accept": "application/vnd.github+json"}

_checked_token = None   # значение токена, которое уже проверяли
_token_ok = False


def reset_token_check() -> None:
    """Сбросить кэш проверки (тесты, смена токена в одном процессе)."""
    global _checked_token, _token_ok
    _checked_token, _token_ok = None, False


def _validate(http, token: str) -> bool:
    try:
        http.get_json(f"{API}/rate_limit", headers={**ACCEPT, "Authorization": f"Bearer {token}"})
        return True
    except Exception as e:  # noqa: BLE001
        if "401" in str(e):
            log.error(
                "GITHUB_TOKEN недействителен (401): истёк или отозван. Работаю анонимно — "
                "60 запросов/час вместо 5000, данных GitHub почти не будет. "
                "Выпустите новый: github.com/settings/tokens (classic, без скоупов) "
                "и положите в backend/.env"
            )
            return False
        log.warning("проверить GITHUB_TOKEN не удалось (%s) — используем как есть", e)
        return True


def github_headers(http=None) -> dict:
    """Заголовки для api.github.com. http нужен для разовой проверки токена."""
    global _checked_token, _token_ok
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return dict(ACCEPT)
    if token != _checked_token:
        _token_ok = _validate(http, token) if http is not None else True
        _checked_token = token
    if _token_ok:
        return {**ACCEPT, "Authorization": f"Bearer {token}"}
    return dict(ACCEPT)
