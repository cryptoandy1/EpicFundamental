"""Он-чейн: кошельки команды/инвесторов (ф.7) + аккумуляция smart money (ф.11).

Требуется бесплатный ключ ETHERSCAN_API_KEY (env) — единый Etherscan v2 API
работает для ethereum/bsc/arbitrum/base/... через параметр chainid.

Ф.7: полная история переводов токена по помеченным кошелькам; отдельно
помечаем выводы на известные горячие кошельки бирж (= вероятная продажа).

Ф.11 (эвристика, честно — это не Nansen): по последним 10k переводов токена
берём топ-50 получателей и проверяем nonce их адресов; nonce <= 2 = «свежий»
кошелёк без другой истории — признак разделения аккаунтов для аккумуляции.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..models import Project, Wallet, WalletFlow
from . import register
from .base import Collector, upsert_metrics

log = logging.getLogger("collectors.wallets")

ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"

CHAIN_IDS = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "optimism": 10,
    "base": 8453,
    "avalanche_c": 43114,
}

# Широко известные горячие кошельки бирж (ethereum). Вывод команды сюда = вероятная продажа.
EXCHANGE_ADDRESSES = {
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance 14",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance 15",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "Binance 16",
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "OKX",
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": "Coinbase 1",
    "0x503828976d22510aad0201ac7ec88293211d23da": "Coinbase 2",
    "0x2910543af39aba0cd09dbb2d50200b3e800a63d2": "Kraken",
    "0x0a869d79a7052c7f1b55a8ebabbea3420f0d1e13": "Kraken 4",
}


def _api_key() -> str:
    return os.environ.get("ETHERSCAN_API_KEY", "")


class _EtherscanMixin(Collector):
    def _escan(self, chain: str, **params) -> list | dict:
        chainid = CHAIN_IDS.get(chain)
        if chainid is None:
            raise ValueError(f"нет chainid для '{chain}'")
        data = self.http.get_json(
            ETHERSCAN_V2,
            params={"chainid": chainid, "apikey": _api_key(), **params},
        )
        if str(data.get("status")) == "0" and data.get("message") not in ("No transactions found",):
            raise RuntimeError(f"etherscan: {data.get('message')} {data.get('result')}")
        return data.get("result") or []


@register
class TeamWalletsCollector(_EtherscanMixin):
    name = "wallets"
    scope = "project"
    description = "История переводов по кошелькам команды/инвесторов + выводы на биржи (ф.7)"

    def backfill(self, project: Project | None = None) -> str:
        assert project is not None
        if not _api_key():
            return "пропуск: нет ETHERSCAN_API_KEY (бесплатный ключ на etherscan.io)"
        wallets = self.session.query(Wallet).filter_by(project_id=project.id).all()
        if not wallets:
            return "нет помеченных кошельков в projects.yaml (заполните из меток Arkham/Etherscan)"

        contracts = json.loads(project.token_contracts or "{}")
        total_flows = 0
        for wallet in wallets:
            try:
                txs = self._escan(
                    wallet.chain,
                    module="account",
                    action="tokentx",
                    address=wallet.address,
                    startblock=0,
                    endblock=99999999,
                    sort="asc",
                    page=1,
                    offset=10000,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("wallet %s: %s", wallet.address, e)
                continue
            wanted_contract = (contracts.get(wallet.chain) or "").lower()
            rows = []
            for tx in txs:
                symbol_ok = (tx.get("tokenSymbol") or "").upper() == project.symbol
                contract_ok = wanted_contract and tx.get("contractAddress", "").lower() == wanted_contract
                if not (symbol_ok or contract_ok):
                    continue
                addr = wallet.address.lower()
                direction = "out" if tx["from"].lower() == addr else "in"
                counterparty = tx["to"] if direction == "out" else tx["from"]
                decimals = int(tx.get("tokenDecimal") or 18)
                amount = int(tx["value"]) / 10**decimals
                rows.append(
                    {
                        "wallet_id": wallet.id,
                        "ts": datetime.fromtimestamp(int(tx["timeStamp"]), tz=timezone.utc).replace(tzinfo=None),
                        "direction": direction,
                        "amount": amount,
                        "token": tx.get("tokenSymbol", ""),
                        "tx_hash": tx["hash"],
                        "counterparty": counterparty,
                        "to_exchange": direction == "out" and counterparty.lower() in EXCHANGE_ADDRESSES,
                    }
                )
            for chunk_start in range(0, len(rows), 400):
                chunk = rows[chunk_start : chunk_start + 400]
                stmt = sqlite_insert(WalletFlow).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["tx_hash", "wallet_id", "direction"])
                self.session.execute(stmt)
            self.session.commit()
            total_flows += len(rows)

        self._aggregate_weekly(project)
        return f"wallets: {len(wallets)} кошельков, {total_flows} переводов"

    def _aggregate_weekly(self, project: Project) -> None:
        """Недельные агрегаты для графика: продано командой всего / на биржи."""
        flows = (
            self.session.query(WalletFlow, Wallet)
            .join(Wallet, WalletFlow.wallet_id == Wallet.id)
            .filter(Wallet.project_id == project.id, WalletFlow.direction == "out")
            .all()
        )
        weekly_out: dict[datetime, float] = defaultdict(float)
        weekly_exchange: dict[datetime, float] = defaultdict(float)
        for flow, _wallet in flows:
            week = (flow.ts - timedelta(days=flow.ts.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            weekly_out[week] += flow.amount
            if flow.to_exchange:
                weekly_exchange[week] += flow.amount
        rows = [
            {"project_id": project.id, "metric": "team_outflow_tokens", "ts": ts, "value": v}
            for ts, v in weekly_out.items()
        ] + [
            {"project_id": project.id, "metric": "team_to_exchange_tokens", "ts": ts, "value": v}
            for ts, v in weekly_exchange.items()
        ]
        upsert_metrics(self.session, rows)


@register
class SmartMoneyCollector(_EtherscanMixin):
    name = "smart_money"
    scope = "project"
    description = "Эвристика аккумуляции свежими кошельками без истории (ф.11)"

    TOP_RECEIVERS = 50

    def backfill(self, project: Project | None = None) -> str:
        assert project is not None
        if not _api_key():
            return "пропуск: нет ETHERSCAN_API_KEY"
        contracts = json.loads(project.token_contracts or "{}")
        if not contracts:
            return "пропуск: token_contracts не задан (для нативных L1-монет неприменимо)"

        chain, contract = next(iter(contracts.items()))
        txs = self._escan(
            chain,
            module="account",
            action="tokentx",
            contractaddress=contract,
            sort="desc",
            page=1,
            offset=10000,
        )
        received: dict[str, float] = defaultdict(float)
        ts_range: list[datetime] = []
        for tx in txs:
            decimals = int(tx.get("tokenDecimal") or 18)
            received[tx["to"].lower()] += int(tx["value"]) / 10**decimals
            ts_range.append(
                datetime.fromtimestamp(int(tx["timeStamp"]), tz=timezone.utc).replace(tzinfo=None)
            )
        if not received:
            return "нет переводов токена"

        top = sorted(received.items(), key=lambda kv: kv[1], reverse=True)[: self.TOP_RECEIVERS]
        fresh_amount = 0.0
        total_amount = sum(a for _, a in top)
        fresh_count = 0
        for addr, amount in top:
            try:
                nonce_hex = self._escan(
                    chain, module="proxy", action="eth_getTransactionCount", address=addr, tag="latest"
                )
                nonce = int(str(nonce_hex), 16)
            except Exception:  # noqa: BLE001
                continue
            if nonce <= 2:  # свежий кошелёк: почти нет исходящей истории
                fresh_amount += amount
                fresh_count += 1

        share = fresh_amount / total_amount if total_amount else 0.0
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        upsert_metrics(
            self.session,
            [
                {
                    "project_id": project.id,
                    "metric": "smart_money_fresh_share",
                    "ts": today,
                    "value": share,
                    "meta": json.dumps(
                        {
                            "fresh_wallets": fresh_count,
                            "sample_top": len(top),
                            "window_from": min(ts_range).isoformat() if ts_range else None,
                        }
                    ),
                }
            ],
        )
        return f"smart_money: {fresh_count}/{len(top)} свежих кошельков, доля объёма {share:.1%}"
