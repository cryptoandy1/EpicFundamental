"""Бэктест лесенки: предсказывал ли скор что-нибудь на самом деле.

До сих пор веса факторов были мнением, а не измерением. Здесь скор считается
ПО СОСТОЯНИЮ НА ДАТУ (compute_ladder(as_of=...)), затем сверяется с тем, что монеты
показали ПОСЛЕ этой даты относительно BTC.

Метрики:
  spearman     — корреляция ранга и последующей доходности сверх BTC (0 = скор бесполезен);
  top/bottom   — средняя избыточная доходность верха и низа лесенки;
  strategy_cum — накопленный результат «держать топ-N равными долями» против HODL BTC.

Честные ограничения (выводятся в summary.caveats):
  * снапшотные метрики (Nansen, ноды, ранг Coinbase, доминация) существуют только
    с августа-сентября 2026 — раньше ранги строятся на GitHub/Trends/TVL/комиссиях/цене;
  * market_cap собран за 365 дней, поэтому fees_to_mcap вглубь истории отсутствует;
  * пул из 9 монет утверждён в 2026 году: это survivorship bias, монеты, умершие
    по дороге, в выборку не попали. Результат — проверка ранжирования, не доходности.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .models import MARKET
from .scoring import _latest, _value_at, compute_ladder

log = logging.getLogger("backtest")

MIN_COINS_PER_DATE = 3  # меньше — ранжировать нечего
DEFAULT_TOP_N = 3


def _ranks(values: list[float]) -> list[float]:
    """Ранги 1..n со средним для одинаковых значений."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Ранговая корреляция (Пирсон по рангам). scipy в зависимостях нет — считаем сами."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx)
    dy = sum((b - my) ** 2 for b in ry)
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy) ** 0.5, 4)


def _forward_return(session: Session, project_id: str, metric: str, start: datetime, end: datetime) -> float | None:
    p0 = _value_at(session, project_id, metric, start)
    p1 = _value_at(session, project_id, metric, end)
    if not p0 or not p1:
        return None
    return p1 / p0 - 1.0


def run_backtest(
    session: Session,
    start: datetime,
    end: datetime,
    step_days: int = 28,
    horizon_days: int = 28,
    top_n: int = DEFAULT_TOP_N,
) -> dict:
    """Прогон по датам с шагом step_days; на каждой — ранг против доходности за horizon_days."""
    dates: list[dict] = []
    strategy_cum, btc_cum = 1.0, 1.0
    cursor = start

    while cursor + timedelta(days=horizon_days) <= end:
        horizon_end = cursor + timedelta(days=horizon_days)
        btc_ret = _forward_return(session, MARKET, "btc_price_usd", cursor, horizon_end)
        if btc_ret is None:
            cursor += timedelta(days=step_days)
            continue

        rows = []
        for row in compute_ladder(session, as_of=cursor):
            if row["score"] is None:
                continue
            ret = _forward_return(session, row["project"], "price_usd", cursor, horizon_end)
            if ret is None:
                continue
            rows.append(
                {
                    "project": row["project"],
                    "symbol": row["symbol"],
                    "score": row["score"],
                    "coverage": row["coverage"],
                    "fwd_return": round(ret, 4),
                    "excess": round(ret - btc_ret, 4),
                }
            )

        if len(rows) < MIN_COINS_PER_DATE:
            cursor += timedelta(days=step_days)
            continue

        rows.sort(key=lambda r: -r["score"])
        n = min(top_n, len(rows) // 2) or 1
        top, bottom = rows[:n], rows[-n:]
        top_excess = sum(r["excess"] for r in top) / len(top)
        top_return = sum(r["fwd_return"] for r in top) / len(top)
        strategy_cum *= 1 + top_return
        btc_cum *= 1 + btc_ret

        dates.append(
            {
                "date": cursor.date().isoformat(),
                "n": len(rows),
                "mean_coverage": round(sum(r["coverage"] for r in rows) / len(rows), 3),
                "btc_return": round(btc_ret, 4),
                "spearman": spearman([r["score"] for r in rows], [r["excess"] for r in rows]),
                "top_excess": round(top_excess, 4),
                "bottom_excess": round(sum(r["excess"] for r in bottom) / len(bottom), 4),
                "top_symbols": [r["symbol"] for r in top],
                "rows": rows,
            }
        )
        cursor += timedelta(days=step_days)

    spearmans = sorted(d["spearman"] for d in dates if d["spearman"] is not None)
    top_excesses = [d["top_excess"] for d in dates]
    # Лонг-шорт (верх минус низ) — честный тест САМОГО ранжирования: он не зависит от
    # того, обгоняли ли альты биткоин как класс. Разница между «скор бесполезен» и
    # «скор работает, но входить в альты было рано» видна только здесь.
    long_short = [d["top_excess"] - d["bottom_excess"] for d in dates]
    median = lambda xs: (  # noqa: E731
        None if not xs else sorted(xs)[len(xs) // 2] if len(xs) % 2 else
        (sorted(xs)[len(xs) // 2 - 1] + sorted(xs)[len(xs) // 2]) / 2
    )
    return {
        "params": {
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "step_days": step_days,
            "horizon_days": horizon_days,
            "top_n": top_n,
        },
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat(),
        "dates": dates,
        "summary": {
            "periods": len(dates),
            # средняя ранговая корреляция: > 0 — скор ранжирует в нужную сторону
            "mean_spearman": round(sum(spearmans) / len(spearmans), 4) if spearmans else None,
            "median_spearman": round(median(spearmans), 4) if spearmans else None,
            "spearman_positive_rate": round(sum(1 for x in spearmans if x > 0) / len(spearmans), 3)
            if spearmans
            else None,
            # главный показатель качества ранжирования, нейтральный к фазе рынка
            "mean_long_short": round(sum(long_short) / len(long_short), 4) if long_short else None,
            "long_short_positive_rate": round(sum(1 for x in long_short if x > 0) / len(long_short), 3)
            if long_short
            else None,
            # доля периодов, где верх лесенки обогнал BTC
            "hit_rate": round(sum(1 for x in top_excesses if x > 0) / len(top_excesses), 3)
            if top_excesses
            else None,
            "mean_top_excess": round(sum(top_excesses) / len(top_excesses), 4) if top_excesses else None,
            "strategy_cum": round(strategy_cum - 1, 4),
            "btc_cum": round(btc_cum - 1, 4),
            "caveats": [
                "Пул утверждён в 2026 — выжившие монеты (survivorship bias).",
                "Снапшотные факторы (Nansen, ноды, Coinbase) есть только с августа 2026.",
                "market_cap собран за 365 дней — fees_to_mcap вглубь истории отсутствует.",
                "Комиссии на сделки и проскальзывание не учтены.",
            ],
        },
    }
