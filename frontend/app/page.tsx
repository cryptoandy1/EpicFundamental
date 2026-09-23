"use client";
import { useEffect, useMemo, useState } from "react";
import Chart from "@/components/Chart";
import { api, EntryGate, MarketOverview } from "@/lib/api";
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

  return (
    <>
      <h2>Обзор рынка — сигнал выхода</h2>
      {data.sell_signal ? (
        <div className="alert danger">
          <b>Пик интереса!</b> Текущий Google-интерес к биткоину — {pct} перцентиль за 5 лет (&ge; 90).
          По вашей стратегии: <b>пик = сливаем</b>.
        </div>
      ) : (
        <div className="alert ok">
          Пика интереса нет: текущий Google-интерес к биткоину — {pct ?? "н/д"} перцентиль за 5 лет
          (сигнал слива при &ge; 90).
        </div>
      )}

      {gate.open ? (
        <div className="alert ok">
          <b>Ворота входа открыты.</b> Альты обгоняют BTC ({gate.alts_beating_btc_30d_pct}% топ-100 за
          30 дней, порог {gate.alts_threshold}%) при падающей доминации BTC — фаза ротации в альты,
          лесенка применима.
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
          <div className="value" style={{ color: pct !== null && pct >= 90 ? t.critical : t.ink }}>
            {pct ?? "н/д"}
          </div>
          <div className="hint">от 5-летнего диапазона (ф.1)</div>
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
          <div className="value" style={{ color: gate.open ? t.good : t.ink }}>
            {gate.open ? "открыты" : "закрыты"}
          </div>
          <div className="hint">
            {gate.alts_beating_btc_30d_pct !== null
              ? `${gate.alts_beating_btc_30d_pct}% альтов обгоняют BTC`
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
