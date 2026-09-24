"use client";
import { useEffect, useMemo, useState } from "react";
import Chart from "@/components/Chart";
import { api, EntryGate, ExitSignal, MarketOverview, Playbook } from "@/lib/api";
import { TOKENS, baseOption, lineSeries, useMode } from "@/lib/theme";

export default function MarketPage() {
  const mode = useMode();
  const t = TOKENS[mode];
  const [data, setData] = useState<MarketOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<MarketOverview>("/api/market/overview").then(setData).catch((e) => setError(String(e)));
  }, []);

  const trendsWeeklyOpt = useMemo(() => {
    if (!data || data.btc_trends_weekly.length === 0) return null;
    const base = baseOption(t);
    return {
      ...base,
      series: [
        lineSeries("Интерес к 'bitcoin' (недели, 5 лет)", data.btc_trends_weekly, {
          areaStyle: { opacity: 0.08 },
        }),
      ],
      yAxis: { ...base.yAxis, max: 100 },
    };
  }, [data, t]);

  const trendsMonthlyOpt = useMemo(() => {
    if (!data || data.btc_trends_monthly.length === 0) return null;
    const base = baseOption(t);
    return {
      ...base,
      series: [lineSeries("Интерес к 'bitcoin' (месяцы, вся история)", data.btc_trends_monthly)],
      yAxis: { ...base.yAxis, max: 100 },
    };
  }, [data, t]);

  const priceOpt = useMemo(() => {
    if (!data || data.btc_price.length === 0) return null;
    const base = baseOption(t);
    return {
      ...base,
      series: [lineSeries("BTC, $ (лог-шкала)", data.btc_price)],
      yAxis: { ...base.yAxis, type: "log" as const, splitLine: { lineStyle: { color: t.grid } } },
    };
  }, [data, t]);

  const coinbaseOpt = useMemo(() => {
    if (!data) return null;
    const base = baseOption(t);
    const hasData = data.coinbase_rank_overall.length + data.coinbase_rank_finance.length > 0;
    if (!hasData) return null;
    return {
      ...base,
      series: [
        lineSeries("Общий топ App Store", data.coinbase_rank_overall, { connectNulls: false }),
        lineSeries("Категория Finance", data.coinbase_rank_finance, { connectNulls: false }),
      ],
      // ранг: 1 — вверху; 201 = «вне топ-200»
      yAxis: { ...base.yAxis, inverse: true, min: 1 },
    };
  }, [data, t]);

  const gateOpt = useMemo(() => {
    const gate = data?.entry_gate;
    if (!gate || gate.alts_series.length + gate.dominance_series.length === 0) return null;
    const base = baseOption(t);
    return {
      ...base,
      series: [
        lineSeries("Доля топ-100, обгоняющих BTC за 30д, %", gate.alts_series),
        lineSeries("Доминация BTC, %", gate.dominance_series),
      ],
      yAxis: { ...base.yAxis, max: 100 },
    };
  }, [data, t]);

  if (error) return <div className="alert danger">API недоступен: {error}. Запустите backend: python -m app serve</div>;
  if (!data) return <div className="empty">Загрузка…</div>;

  const pct = data.trends_percentile;
  // подстраховка на случай снапшота, снятого до появления ворот (кэш браузера)
  const gate: EntryGate = data.entry_gate ?? {
    btc_dominance_pct: null,
    btc_dominance_4w_ago: null,
    dominance_lookback_days: 28,
    dominance_falling: false,
    alts_beating_btc_30d_pct: null,
    alts_threshold: 50,
    open: false,
    dominance_series: [],
    alts_series: [],
  };
  const gateState = gate.gate_state ?? (gate.open ? "open" : "closed");
  const historyDays = gate.history_days ?? gate.dominance_series.length;
  // старый снапшот знает только булев sell_signal — разворачиваем его в трёхуровневый
  const exit: ExitSignal = data.exit_signal ?? {
    tier: data.sell_signal ? "sell" : "ok",
    trends_percentile: pct,
    coinbase_rank_overall_latest: null,
    thresholds: {},
    reasons: [],
  };
  const play: Playbook = data.playbook ?? {
    state: data.sell_signal ? "EXIT" : gate.open ? "ROTATE" : "HOLD_BTC",
    text: "Снапшот снят до появления этого блока — обновите экспорт.",
  };
  const exitColor = exit.tier === "sell" ? t.critical : exit.tier === "warming" ? t.warning : t.ink;

  return (
    <>
      <h2>Что делать сейчас</h2>
      <div
        className={
          play.state === "EXIT" ? "alert danger" : play.state === "ROTATE" ? "alert ok" : "alert"
        }
      >
        <b>{play.text}</b>
      </div>

      <h2>Сигнал выхода в стейблы</h2>
      {exit.tier === "sell" ? (
        <div className="alert danger">
          <b>Пик интереса — сливаем.</b> Сработало: {exit.reasons.join("; ")}. Это выход из ВСЕГО
          портфеля в стейблы, а не ротация между монетами.
        </div>
      ) : exit.tier === "warming" ? (
        <div className="alert warn">
          <b>Разогрев.</b> {exit.reasons.join("; ")}. Продавать рано — подготовьте план выхода:
          уровни и доли траншей.
        </div>
      ) : (
        <div className="alert ok">
          Эйфории нет: Google-интерес к биткоину {pct ?? "н/д"} перцентиль за 5 лет (слив при &ge;{" "}
          {exit.thresholds.trends_sell_pct ?? 90}), Coinbase{" "}
          {exit.coinbase_rank_overall_latest !== null
            ? exit.coinbase_rank_overall_latest > 200
              ? "вне топ-200"
              : `#${exit.coinbase_rank_overall_latest}`
            : "н/д"}{" "}
          (слив при &le; #{exit.thresholds.coinbase_sell_rank ?? 10}).
        </div>
      )}

      <h2>Ворота входа в лесенку</h2>
      {gateState === "open" ? (
        <div className="alert ok">
          <b>Ворота входа открыты.</b> Альты обгоняют BTC ({gate.alts_beating_btc_30d_pct}% топ-100 за
          30 дней, порог {gate.alts_threshold}%) при падающей доминации BTC — фаза ротации в альты,
          лесенка применима.
        </div>
      ) : gateState === "warming" ? (
        <div className="alert warn">
          <b>Разогрев: альты обгоняют BTC, доминация не подтверждена.</b>{" "}
          {gate.alts_beating_btc_30d_pct}% топ-100 за 30 дней при пороге {gate.alts_threshold}%, но
          истории доминации {historyDays} из {gate.dominance_lookback_days} дн. Одна доля альтов даёт
          ложный сигнал на отскоке — ждём подтверждения по доминации.
        </div>
      ) : (
        <div className="alert">
          <b>Ворота входа закрыты — сезон биткоина.</b>{" "}
          {gate.alts_beating_btc_30d_pct !== null
            ? `Обгоняют BTC за 30 дней ${gate.alts_beating_btc_30d_pct}% топ-100 (нужно ≥ ${gate.alts_threshold}%)`
            : "Нет данных о доле альтов, обгоняющих BTC"}
          {gate.btc_dominance_pct !== null &&
            `, доминация BTC ${gate.btc_dominance_pct.toFixed(1)}%${
              gate.dominance_falling ? " (падает)" : " (не падает)"
            }`}
          . Ротация в альты преждевременна: держите ядро в BTC.
        </div>
      )}

      <div className="stat-row">
        <div className="stat">
          <div className="label">Google Trends «bitcoin», перцентиль</div>
          <div className="value" style={{ color: exitColor }}>
            {pct ?? "н/д"}
          </div>
          <div className="hint">
            от 5-летнего диапазона (ф.1); слив при &ge; {exit.thresholds.trends_sell_pct ?? 90}
          </div>
        </div>
        <div className="stat">
          <div className="label">BTC, последняя цена</div>
          <div className="value">
            {data.btc_price.length
              ? `$${Math.round(data.btc_price[data.btc_price.length - 1][1]).toLocaleString()}`
              : "н/д"}
          </div>
          <div className="hint">Binance, дневные свечи</div>
        </div>
        <div className="stat">
          <div className="label">Coinbase в App Store</div>
          <div className="value">
            {data.coinbase_rank_overall.length
              ? (() => {
                  const r = data.coinbase_rank_overall[data.coinbase_rank_overall.length - 1][1];
                  return r > 200 ? "вне топ-200" : `#${r}`;
                })()
              : "н/д"}
          </div>
          <div className="hint">в топ-10 на пиках маний (ф.2)</div>
        </div>
        <div className="stat">
          <div className="label">Ворота входа в лесенку</div>
          <div
            className="value"
            style={{
              color: gateState === "open" ? t.good : gateState === "warming" ? t.warning : t.ink,
            }}
          >
            {gateState === "open" ? "открыты" : gateState === "warming" ? "разогрев" : "закрыты"}
          </div>
          <div className="hint">
            {gate.alts_beating_btc_30d_pct !== null
              ? `${gate.alts_beating_btc_30d_pct}% альтов обгоняют BTC; истории ${historyDays} из ${gate.dominance_lookback_days} дн.`
              : "нет данных"}
          </div>
        </div>
      </div>

      <div className="grid2">
        <div className="card">
          <h3>Google Trends «bitcoin» — 5 лет</h3>
          <p className="sub">ф.1: пик интереса = сигнал слива</p>
          {trendsWeeklyOpt ? <Chart option={trendsWeeklyOpt} /> : <div className="empty">нет данных — запустите backfill (btc_trends)</div>}
        </div>
        <div className="card">
          <h3>Google Trends «bitcoin» — вся история</h3>
          <p className="sub">месячные точки с 2004 — видно прошлые циклы</p>
          {trendsMonthlyOpt ? <Chart option={trendsMonthlyOpt} /> : <div className="empty">нет данных</div>}
        </div>
        <div className="card">
          <h3>Цена BTC</h3>
          <p className="sub">вся история с Binance, лог-шкала</p>
          {priceOpt ? <Chart option={priceOpt} /> : <div className="empty">нет данных</div>}
        </div>
        <div className="card">
          <h3>Ворота входа: альты vs BTC</h3>
          <p className="sub">
            когда начинать лесенку: доля топ-100, обгоняющих BTC за 30 дней (порог {gate.alts_threshold}%),
            и доминация BTC; копится с 23.09.2026
          </p>
          {gateOpt ? (
            <Chart option={gateOpt} />
          ) : (
            <div className="empty">нет данных — запустите update (altseason)</div>
          )}
        </div>
        <div className="card">
          <h3>Ранг Coinbase в App Store</h3>
          <p className="sub">ф.2: прокси скачиваний; ранг 1 — вверху; 201 = вне топ-200</p>
          {coinbaseOpt ? (
            <Chart option={coinbaseOpt} />
          ) : (
            <div className="empty">нет данных — запустите backfill (coinbase_app)</div>
          )}
        </div>
      </div>
    </>
  );
}
