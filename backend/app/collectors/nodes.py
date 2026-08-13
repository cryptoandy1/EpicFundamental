"""Ноды и валидаторы (ф.8, 9).

Адаптеры по сетям: ethereum (beaconcha.in — есть история), solana / near /
avalanche / cosmos-SDK-сети (снапшот; история копится с момента запуска —
ретроспективы у бесплатных API нет, это честное ограничение).

Ф.9: ищем в мониках/аккаунтах валидаторов имена бирж (Binance, OKX, ...);
первое появление = событие "validator" (оверлей на график нод).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..models import Project
from . import register
from .base import Collector, upsert_events, upsert_metrics

log = logging.getLogger("collectors.nodes")

EXCHANGE_PATTERNS = [
    "binance", "okx", "okex", "coinbase", "kraken", "huobi", "htx",
    "bybit", "kucoin", "upbit", "bitfinex", "crypto.com", "gate.io",
]

COSMOS_LCD = {
    "celestia": "https://celestia-rest.publicnode.com",
    "cosmos": "https://cosmos-rest.publicnode.com",
    "osmosis": "https://osmosis-rest.publicnode.com",
    "injective": "https://injective-rest.publicnode.com",
    "sei": "https://sei-rest.publicnode.com",
    "dydx": "https://dydx-rest.publicnode.com",
}


def _today() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


@register
class NodesCollector(Collector):
    name = "nodes"
    scope = "project"
    description = "Число нод/валидаторов + подключение биржевых валидаторов (ф.8, 9)"

    def backfill(self, project: Project | None = None) -> str:
        assert project is not None
        chain = project.chain
        if chain == "ethereum":
            return self._ethereum(project)
        if chain == "solana":
            return self._solana(project)
        if chain == "near":
            return self._near(project)
        if chain == "avalanche":
            return self._avalanche(project)
        if chain in COSMOS_LCD:
            return self._cosmos(project, COSMOS_LCD[chain])
        return f"нет адаптера для сети '{chain}' (история будет копиться после добавления)"

    # --- адаптеры ---

    def _ethereum(self, project: Project) -> str:
        # beaconcha.in: пробуем исторический график, иначе снапшот
        try:
            data = self.http.get_json("https://beaconcha.in/api/v1/chart/validators")
            points = data.get("data") or []
            rows = []
            for p in points:
                if isinstance(p, dict):
                    ts_raw = p.get("x") or p.get("ts") or p.get("time")
                    val = p.get("y") or p.get("value")
                elif isinstance(p, (list, tuple)) and len(p) >= 2:
                    ts_raw, val = p[0], p[1]
                else:
                    continue
                if ts_raw is None or val is None:
                    continue
                ts_raw = float(ts_raw)
                if ts_raw > 1e12:  # миллисекунды
                    ts_raw /= 1000
                ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc).replace(tzinfo=None)
                rows.append(
                    {"project_id": project.id, "metric": "node_count", "ts": ts, "value": float(val)}
                )
            if rows:
                n = upsert_metrics(self.session, rows)
                return f"ethereum: история валидаторов, {n} точек"
        except Exception as e:  # noqa: BLE001
            log.warning("beaconcha.in chart: %s", e)
        data = self.http.get_json("https://beaconcha.in/api/v1/epoch/latest")
        count = float((data.get("data") or {}).get("validatorscount") or 0)
        upsert_metrics(
            self.session,
            [{"project_id": project.id, "metric": "node_count", "ts": _today(), "value": count}],
        )
        return f"ethereum: снапшот, {count:.0f} валидаторов"

    def _solana(self, project: Project) -> str:
        data = self.http.post_json(
            "https://api.mainnet-beta.solana.com",
            {"jsonrpc": "2.0", "id": 1, "method": "getVoteAccounts"},
        )
        result = data.get("result") or {}
        count = len(result.get("current", [])) + len(result.get("delinquent", []))
        upsert_metrics(
            self.session,
            [{"project_id": project.id, "metric": "node_count", "ts": _today(), "value": float(count)}],
        )
        return f"solana: снапшот, {count} валидаторов (имена — только через validators.app, платно)"

    def _near(self, project: Project) -> str:
        data = self.http.post_json(
            "https://rpc.mainnet.near.org",
            {"jsonrpc": "2.0", "id": "1", "method": "validators", "params": [None]},
        )
        validators = (data.get("result") or {}).get("current_validators", [])
        names = [v.get("account_id", "") for v in validators]
        self._store(project, len(validators), names)
        return f"near: снапшот, {len(validators)} валидаторов"

    def _avalanche(self, project: Project) -> str:
        data = self.http.post_json(
            "https://api.avax.network/ext/bc/P",
            {"jsonrpc": "2.0", "id": 1, "method": "platform.getCurrentValidators", "params": {}},
        )
        validators = (data.get("result") or {}).get("validators", [])
        self._store(project, len(validators), [])  # у P-Chain нет имён
        return f"avalanche: снапшот, {len(validators)} валидаторов"

    def _cosmos(self, project: Project, lcd: str) -> str:
        validators: list[dict] = []
        next_key = ""
        while True:
            params = {"status": "BOND_STATUS_BONDED", "pagination.limit": "500"}
            if next_key:
                params["pagination.key"] = next_key
            data = self.http.get_json(f"{lcd}/cosmos/staking/v1beta1/validators", params=params)
            validators += data.get("validators", [])
            next_key = (data.get("pagination") or {}).get("next_key") or ""
            if not next_key:
                break
        names = [(v.get("description") or {}).get("moniker", "") for v in validators]
        self._store(project, len(validators), names)
        return f"{project.chain}: снапшот, {len(validators)} активных валидаторов"

    # --- общее ---

    def _store(self, project: Project, count: int, names: list[str]) -> None:
        upsert_metrics(
            self.session,
            [{"project_id": project.id, "metric": "node_count", "ts": _today(), "value": float(count)}],
        )
        if not names:
            return
        found: dict[str, list[str]] = {}
        for name in names:
            low = name.lower()
            for pattern in EXCHANGE_PATTERNS:
                if pattern in low:
                    found.setdefault(pattern, []).append(name)
        upsert_metrics(
            self.session,
            [
                {
                    "project_id": project.id,
                    "metric": "exchange_validators_count",
                    "ts": _today(),
                    "value": float(sum(len(v) for v in found.values())),
                    "meta": json.dumps(found, ensure_ascii=False),
                }
            ],
        )
        # событие — при ПЕРВОМ появлении биржи в наборе валидаторов
        from ..models import Event

        seen: set[str] = {
            e.title.split(":")[0].strip().lower()
            for e in self.session.query(Event)
            .filter_by(project_id=project.id, type="validator")
            .all()
        }
        events = []
        for pattern, hits in found.items():
            if pattern not in seen:
                events.append(
                    {
                        "project_id": project.id,
                        "type": "validator",
                        "ts": _today(),
                        "title": f"{pattern}: подключился валидатор",
                        "value": float(len(hits)),
                        "meta": json.dumps(hits, ensure_ascii=False),
                    }
                )
        upsert_events(self.session, events)
