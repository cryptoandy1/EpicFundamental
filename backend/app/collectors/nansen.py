"""Nansen API (ф.11): smart money, потоки бирж и свежих кошельков.

Что удалось получить по НАТИВНЫМ монетам пула (проверено на реальных данных):

1. Перпы Hyperliquid — `perp-screener` (1 кредит за ВЕСЬ пул): позиции Smart Money
   (лонги/шорты в USD и число аккаунтов), изменение позиции за окно, объём, funding,
   открытый интерес. Единственный smart-money-сигнал, сравнимый по всем 9 монетам.
   Плюс `tgm/position-intelligence` (1 кредит/монету) — киты и публичные фигуры.
2. `tgm/flow-intelligence` (1 кредит/монету, 6 сетей): нетто-потоки за 7 дней по
   сегментам. Достоверны `exchange` и `fresh_wallets`; сегмент smart_trader для
   плейсхолдера нативной монеты `0xeee…` возвращает ОДНО значение на все EVM-сети
   (баг Nansen) — поэтому не пишется.
3. Спот Smart Money (`smart-money/holdings` + `netflow`, 5 кредитов каждый) —
   когорта реально существует только для SOL/AVAX/SEI, поэтому это панель на
   странице монеты, а не фактор лесенки.

Чего у Nansen НЕТ для нашего пула: `tgm/flows`, `tgm/holders`, `token-screener`
нативные токены не поддерживают («This endpoint does not support native tokens»),
поэтому дневной истории холдингов SM по нашим монетам не существует.

Истории у этих метрик нет: Backtesting API (`token-screener/historical*`,
`tgm/historical-token-flow-summary`) на нашем плане отвечает 404, а обычные
эндпоинты отдают только текущее окно. Поэтому снапшоты копятся вперёд: фактор
считается как среднее снапшотов за последние 28 дней — с первого прогона он уже
работает (одна точка), а через месяц становится устойчивым.

Кредиты — разовый грант (Pro: 2000 + докупка), поэтому прогон разделён на две части
с собственным ритмом, и лишнего не тратится, даже если запускать update ежедневно:

* ЛЁГКАЯ часть (8 кредитов) — то, из чего считаются факторы лесенки: перп-скринер (2)
  и flow-intelligence (6). Ритм — NANSEN_MIN_INTERVAL_DAYS, по умолчанию 1 (ежедневно):
  чем больше снапшотов, тем устойчивее среднее за 28 дней.
* ПОЛНАЯ часть (+19 кредитов) — справочные панели на странице монеты: позиции китов
  (position-intelligence, 9) и спотовая когорта Smart Money (holdings + netflow, 10).
  Ритм — NANSEN_FULL_INTERVAL_DAYS, по умолчанию 7 (раз в неделю).

Жёсткие выключатели: NANSEN_WHALES=0, NANSEN_SPOT=0. Резерв NANSEN_MIN_CREDITS
(по умолчанию 100) — ниже него коллектор останавливается на любом шаге.
Отметки о прогонах — рыночные метрики nansen_run_light / nansen_run_full.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from ..config import load_config
from ..models import Project
from . import register
from .base import Collector, last_metric_ts, upsert_metrics

log = logging.getLogger("collectors.nansen")

API = "https://api.nansen.ai/api/v1"
CREDITS_METRIC = "nansen_credits_remaining"
RUN_LIGHT_METRIC = "nansen_run_light"  # отметка последнего лёгкого прогона (перпы + потоки)
RUN_FULL_METRIC = "nansen_run_full"  # отметка последнего полного (плюс киты и спот)

# сети, где живёт спотовая когорта Smart Money (проверено: SUI/NEAR/INJ не поддерживаются)
SM_SPOT_CHAINS = ["solana", "avalanche", "sei"]
FI_SEGMENTS = {  # сегмент flow-intelligence -> суффикс метрики (smart_trader исключён: баг)
    "exchange": "exchange",
    "fresh_wallets": "fresh_wallets",
    "top_pnl": "top_pnl",
    "whale": "whale",
    "public_figure": "public_figure",
}
PERP_PAGE_SIZE = 1000  # в перп-скринере ~420 контрактов: меньшая страница теряет мелкие монеты


def _api_key() -> str:
    return os.environ.get("NANSEN_API_KEY", "")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def nansen_of(project: Project) -> dict | None:
    """{chain, token_address} из projects.yaml или None, если сеть не покрыта."""
    for p in load_config().get("projects", []):
        if p.get("id") == project.id:
            cfg = p.get("nansen")
            return dict(cfg) if isinstance(cfg, dict) else None
    return None


def hl_symbol(project: Project) -> str:
    for p in load_config().get("projects", []):
        if p.get("id") == project.id:
            return str(p.get("hyperliquid_symbol") or project.symbol).upper()
    return (project.symbol or "").upper()


def _today() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


class NansenBudgetExceeded(RuntimeError):
    """Остаток кредитов ниже резерва — прекращаем расход."""


@register
class NansenCollector(Collector):
    name = "nansen"
    scope = "market"  # один прогон на весь пул: перп-скринер общий для всех монет
    description = "Nansen: SM-перпы Hyperliquid, потоки бирж/свежих кошельков, SM на споте (ф.11)"

    def __init__(self, session, http):
        super().__init__(session, http)
        self.credits: float | None = None
        self.spent = 0

    # --- транспорт ---

    def _post(self, path: str, body: dict) -> dict:
        min_credits = _env_int("NANSEN_MIN_CREDITS", 100)
        if self.credits is not None and self.credits < min_credits:
            raise NansenBudgetExceeded(f"осталось {self.credits:.0f} < резерва {min_credits}")
        resp = self.http.post(
            f"{API}/{path}",
            body,
            headers={"apikey": _api_key(), "Content-Type": "application/json"},
        )
        remaining = resp.headers.get("X-Nansen-Credits-Remaining")
        if remaining:
            try:
                self.credits = float(remaining)
                upsert_metrics(
                    self.session,
                    [{"metric": CREDITS_METRIC, "ts": _today(), "value": self.credits}],
                )
            except ValueError:
                pass
        used = resp.headers.get("X-Nansen-Credits-Used")
        if used:
            try:
                self.spent += int(float(used))
            except ValueError:
                pass
        if resp.status_code >= 400:
            raise RuntimeError(f"nansen {path}: {resp.status_code} {resp.text[:200]}")
        return resp.json()

    def _account_credits(self) -> float | None:
        resp = self.http.get(
            f"{API}/account", headers={"apikey": _api_key()}
        )
        if resp.status_code >= 400:
            return None
        value = resp.json().get("credits_remaining")
        self.credits = _num(value)
        if self.credits is not None:
            upsert_metrics(
                self.session, [{"metric": CREDITS_METRIC, "ts": _today(), "value": self.credits}]
            )
        return self.credits

    # --- источники ---

    def _perp_screener(self, days: int = 7, trader_type: str = "sm") -> dict[str, dict]:
        """{символ Hyperliquid: строка} за последние `days` дней."""
        now = datetime.now(timezone.utc)
        data = self._post(
            "perp-screener",
            {
                "date": {
                    "from": (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                "filters": {"trader_type": trader_type},
                "pagination": {"page": 1, "per_page": PERP_PAGE_SIZE},
            },
        )
        return {str(r.get("token_symbol", "")).upper(): r for r in (data.get("data") or [])}

    def _perp_rows(self, project: Project, sm: dict | None, allt: dict | None, ts: datetime) -> list[dict]:
        rows: list[dict] = []

        def add(metric: str, value) -> None:
            v = _num(value)
            if v is not None:
                rows.append({"project_id": project.id, "metric": metric, "ts": ts, "value": v})

        if sm:
            longs = _num(sm.get("current_smart_money_position_longs_usd")) or 0.0
            shorts = abs(_num(sm.get("current_smart_money_position_shorts_usd")) or 0.0)
            add("nansen_perp_sm_longs_usd", longs)
            add("nansen_perp_sm_shorts_usd", shorts)
            add("nansen_perp_sm_longs_count", sm.get("smart_money_longs_count"))
            add("nansen_perp_sm_shorts_count", sm.get("smart_money_shorts_count"))
            add("nansen_perp_sm_net_change_usd", sm.get("net_position_change"))
            add("nansen_perp_sm_traders", sm.get("trader_count"))
            if longs + shorts > 0:
                add("nansen_perp_sm_skew", (longs - shorts) / (longs + shorts))
        if allt:
            add("nansen_perp_funding", allt.get("funding"))
            add("nansen_perp_oi_usd", allt.get("open_interest"))
            add("nansen_perp_volume_usd", allt.get("volume"))
            add("nansen_perp_pressure_usd", allt.get("buy_sell_pressure"))
            add("nansen_perp_traders", allt.get("trader_count"))
        return rows

    def _position_intelligence(self, project: Project, symbol: str, ts: datetime) -> list[dict]:
        data = self._post("tgm/position-intelligence", {"token_address": symbol})
        items = data.get("data") or []
        if not items:
            return []
        d = items[0]
        pairs = {
            "nansen_perp_whale_longs_usd": d.get("whale_longs_usd"),
            "nansen_perp_whale_shorts_usd": d.get("whale_shorts_usd"),
            "nansen_perp_pf_longs_usd": d.get("public_figure_longs_usd"),
            "nansen_perp_pf_shorts_usd": d.get("public_figure_shorts_usd"),
        }
        return [
            {"project_id": project.id, "metric": m, "ts": ts, "value": v}
            for m, v in ((m, _num(v)) for m, v in pairs.items())
            if v is not None
        ]

    def _flow_rows(self, project: Project, payload: dict, ts: datetime, meta: str) -> list[dict]:
        items = payload.get("data") or []
        if not items:
            return []
        d = items[0]
        rows = []
        for segment, suffix in FI_SEGMENTS.items():
            value = _num(d.get(f"{segment}_net_flow_usd"))
            if value is not None:
                rows.append(
                    {
                        "project_id": project.id,
                        "metric": f"nansen_fi7d_{suffix}_netflow_usd",
                        "ts": ts,
                        "value": value,
                        "meta": meta,
                    }
                )
            count = _num(d.get(f"{segment}_wallet_count"))
            if count:  # у exchange/fresh_wallets всегда 0 — не засоряем
                rows.append(
                    {
                        "project_id": project.id,
                        "metric": f"nansen_fi7d_{suffix}_wallets",
                        "ts": ts,
                        "value": count,
                        "meta": meta,
                    }
                )
        return rows

    def _flow_intelligence(self, project: Project, cfg: dict, ts: datetime) -> list[dict]:
        payload = self._post(
            "tgm/flow-intelligence",
            {"chain": cfg["chain"], "token_address": cfg["token_address"], "timeframe": "7d"},
        )
        return self._flow_rows(project, payload, ts, json.dumps(cfg, ensure_ascii=False))

    def _sm_spot(self, projects: list[Project], ts: datetime) -> tuple[list[dict], int]:
        """Спотовые холдинги и потоки Smart Money — только для сетей SM_SPOT_CHAINS."""
        wanted: dict[tuple[str, str], Project] = {}
        for p in projects:
            cfg = nansen_of(p)
            if cfg and cfg.get("chain") in SM_SPOT_CHAINS:
                wanted[(cfg["chain"], (p.symbol or "").upper())] = p
        if not wanted:
            return [], 0

        chains = sorted({chain for chain, _ in wanted})
        base = {
            "chains": chains,
            "filters": {"include_native_tokens": True, "include_stablecoins": False},
            "pagination": {"page": 1, "per_page": 100},
        }
        rows: list[dict] = []
        matched: set[str] = set()

        holdings = self._post("smart-money/holdings", {**base, "order_by": [{"field": "value_usd", "direction": "DESC"}]})
        for r in holdings.get("data") or []:
            key = (str(r.get("chain")), str(r.get("token_symbol", "")).upper())
            project = wanted.get(key)
            if project is None:
                continue
            matched.add(project.id)
            for metric, value in (
                ("nansen_sm_holdings_usd", r.get("value_usd")),
                ("nansen_sm_holders", r.get("holders_count")),
            ):
                v = _num(value)
                if v is not None:
                    rows.append({"project_id": project.id, "metric": metric, "ts": ts, "value": v})

        netflow = self._post("smart-money/netflow", {**base, "order_by": [{"field": "market_cap_usd", "direction": "DESC"}]})
        for r in netflow.get("data") or []:
            key = (str(r.get("chain")), str(r.get("token_symbol", "")).upper())
            project = wanted.get(key)
            if project is None:
                continue
            matched.add(project.id)
            for metric, value in (
                ("nansen_sm_netflow_7d_usd", r.get("net_flow_7d_usd")),
                ("nansen_sm_netflow_30d_usd", r.get("net_flow_30d_usd")),
                ("nansen_sm_traders_30d", r.get("trader_count")),
            ):
                v = _num(value)
                if v is not None:
                    rows.append({"project_id": project.id, "metric": metric, "ts": ts, "value": v})
        return rows, len(matched)

    # --- прогоны ---

    def _snapshot(self, projects: list[Project], with_heavy: bool) -> tuple[list[dict], list[str]]:
        ts = _today()
        rows: list[dict] = []
        notes: list[str] = []

        sm_rows = self._perp_screener(trader_type="sm")
        all_rows = self._perp_screener(trader_type="all")
        perp_hits = 0
        for project in projects:
            symbol = hl_symbol(project)
            sm, allt = sm_rows.get(symbol), all_rows.get(symbol)
            if not sm and not allt:
                continue
            perp_hits += 1
            rows += self._perp_rows(project, sm, allt, ts)
        notes.append(f"перпы {perp_hits}/{len(projects)}")

        if with_heavy and _env_int("NANSEN_WHALES", 1):
            whales = 0
            for project in projects:
                if hl_symbol(project) not in all_rows:
                    continue
                try:
                    extra = self._position_intelligence(project, hl_symbol(project), ts)
                except NansenBudgetExceeded:
                    raise
                except Exception as e:  # noqa: BLE001
                    log.warning("position-intelligence %s: %s", project.id, e)
                    continue
                rows += extra
                whales += 1 if extra else 0
            notes.append(f"киты {whales}")

        fi_hits = 0
        for project in projects:
            cfg = nansen_of(project)
            if not cfg:
                continue
            try:
                extra = self._flow_intelligence(project, cfg, ts)
            except NansenBudgetExceeded:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("flow-intelligence %s: %s", project.id, e)
                continue
            rows += extra
            fi_hits += 1 if extra else 0
        notes.append(f"потоки {fi_hits}")

        if with_heavy and _env_int("NANSEN_SPOT", 1):
            try:
                spot_rows, spot_hits = self._sm_spot(projects, ts)
                rows += spot_rows
                notes.append(f"SM спот {spot_hits}")
            except NansenBudgetExceeded:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("smart-money spot: %s", e)
                notes.append("SM спот: ошибка")
        if not with_heavy:
            notes.append("лёгкий прогон (киты и спот — по своему ритму)")

        rows.append({"metric": RUN_LIGHT_METRIC, "ts": ts, "value": 1.0})
        if with_heavy:
            rows.append({"metric": RUN_FULL_METRIC, "ts": ts, "value": 1.0})
        return rows, notes

    def _days_since(self, metric: str) -> int | None:
        last = last_metric_ts(self.session, "_market", metric)
        return None if last is None else (_today() - last).days

    def _run(self, with_heavy: bool) -> str:
        if not _api_key():
            return "пропуск: нет NANSEN_API_KEY"
        projects = self.session.query(Project).filter_by(approved=True).all()
        if not projects:
            return "пул пуст"

        min_credits = _env_int("NANSEN_MIN_CREDITS", 100)
        credits = self._account_credits()
        if credits is not None and credits < min_credits:
            return f"пропуск: осталось {credits:.0f} кредитов (резерв {min_credits})"

        rows: list[dict] = []
        notes: list[str] = []
        try:
            snap_rows, snap_notes = self._snapshot(projects, with_heavy)
            rows += snap_rows
            notes += snap_notes
        except NansenBudgetExceeded as e:
            notes.append(f"ОСТАНОВЛЕНО: {e}")
        finally:
            upsert_metrics(self.session, rows)

        left = f"{self.credits:.0f}" if self.credits is not None else "?"
        return f"nansen: {'; '.join(notes)}; потрачено {self.spent}, осталось {left}"

    def backfill(self, project: Project | None = None) -> str:
        # исторических данных у Nansen для наших метрик нет (Backtesting API -> 404),
        # поэтому backfill = полный снапшот, но без проверки интервалов
        return self._run(with_heavy=True)

    def update(self, project: Project | None = None) -> str:
        """Лёгкая часть — раз в NANSEN_MIN_INTERVAL_DAYS, тяжёлая — раз в
        NANSEN_FULL_INTERVAL_DAYS; можно спокойно ставить в ежедневный планировщик."""
        if not _api_key():
            return "пропуск: нет NANSEN_API_KEY"
        light_interval = _env_int("NANSEN_MIN_INTERVAL_DAYS", 1)
        full_interval = _env_int("NANSEN_FULL_INTERVAL_DAYS", 7)
        age_light = self._days_since(RUN_LIGHT_METRIC)
        age_full = self._days_since(RUN_FULL_METRIC)
        light_due = age_light is None or age_light >= light_interval
        full_due = age_full is None or age_full >= full_interval
        # тяжёлая часть проверяется отдельно: иначе ежедневный прогон «съедал» бы отметку
        # и полная часть (киты + спот) не запускалась бы никогда
        if not light_due and not full_due:
            return f"пропуск: обновлялось {age_light} дн. назад (интервалы {light_interval}/{full_interval})"
        return self._run(with_heavy=full_due)
