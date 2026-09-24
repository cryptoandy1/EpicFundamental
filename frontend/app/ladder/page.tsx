"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, Backtest, LadderRow } from "@/lib/api";

const pct = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? "н/д" : `${(v * 100).toFixed(digits)}%`;

const FACTOR_LABELS: Record<string, string> = {
  github_core_devs: "GitHub ядро: активные разработчики, momentum (ф.5)",
  github_ecosystem: "GitHub экосистема: новые репо, momentum (ф.5)",
  trends_momentum: "Trends momentum (ф.14)",
  mentions_momentum: "СМИ momentum (ф.10)",
  node_growth: "Рост нод (ф.8)",
  unlock_pressure: "Навес разлоков (ф.7)",
  team_selling: "Продажи команды (ф.7)",
  fresh_wallets_flow: "Свежие кошельки 7д / капа (Nansen, ф.11)",
  exchange_flow: "Приток на биржи 7д / капа (Nansen; выше = навес)",
  sm_perp_skew: "Smart money перпы: лонг−шорт (Nansen)",
  discord_activity: "Discord (ф.12)",
  tvl_momentum: "TVL momentum",
  fees_momentum: "Комиссии momentum",
  rel_strength_btc: "Сила vs BTC, 90д",
  fees_to_mcap: "Комиссии/капа, годовые",
};

export default function LadderPage() {
  const [rows, setRows] = useState<LadderRow[] | null>(null);
  const [backtest, setBacktest] = useState<Backtest | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<LadderRow[]>("/api/ladder").then(setRows).catch((e) => setError(String(e)));
    api<Backtest>("/api/backtest").then(setBacktest).catch(() => null);
  }, []);

  if (error) return <div className="alert danger">API недоступен: {error}</div>;
  if (!rows) return <div className="empty">Загрузка…</div>;

  const factorKeys = rows.length ? Object.keys(rows[0].factors) : [];

  return (
    <>
      <h2>Лесенка — очередность входа</h2>
      <p style={{ color: "var(--muted)" }}>
        Композитный скор: каждый фактор — перцентиль 0–100 по пулу, взвешенная сумма (веса в
        projects.yaml → scoring; отрицательный вес = штраф). Факторы без данных не учитываются.
      </p>
      <div className="card" style={{ overflowX: "auto" }}>
        <table className="data">
          <thead>
            <tr>
              <th>#</th>
              <th>Монета</th>
              <th>Скор</th>
              <th className="num" title="доля веса скора, стоящая на реальных данных">
                Покрытие
              </th>
              {factorKeys.map((f) => (
                <th key={f} className="num" title={f}>
                  {FACTOR_LABELS[f] ?? f}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.project}>
                <td>{i + 1}</td>
                <td>
                  <Link href={`/project/${r.project}`}>
                    <b>{r.symbol}</b>
                  </Link>{" "}
                  <span style={{ color: "var(--muted)" }}>{r.name}</span>
                </td>
                <td style={{ minWidth: 160 }}>
                  {r.score !== null ? (
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <div className="score-bar" style={{ flex: 1 }}>
                        <div style={{ width: `${r.score}%` }} />
                      </div>
                      <b>{r.score.toFixed(1)}</b>
                    </div>
                  ) : (
                    "нет данных"
                  )}
                </td>
                <td className="num" style={{ color: r.coverage < 1 ? "var(--muted)" : undefined }}>
                  {(r.coverage * 100).toFixed(0)}%
                  <span style={{ fontSize: 11 }}> ({r.factors_available}/{r.factors_total})</span>
                </td>
                {factorKeys.map((f) => {
                  const cell = r.factors[f];
                  const known = cell?.percentile !== null && cell?.percentile !== undefined;
                  return (
                    <td key={f} className="num" style={{ color: known ? undefined : "var(--muted)" }}>
                      {known ? (
                        cell.percentile!.toFixed(0)
                      ) : (
                        <span title="нет данных — нейтральный перцентиль 50">50*</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <div className="empty">Пул пуст</div>}
      </div>
      <p style={{ color: "var(--muted)", fontSize: 12 }}>
        В ячейках — перцентиль фактора по пулу (для штрафных факторов выше = хуже). «50*» — данных
        нет, фактор считается нейтральным (не помогает и не вредит), чтобы монеты с разным покрытием
        сравнивались честно. «Покрытие» — доля веса скора на реальных данных.
      </p>

      {backtest?.summary && (
        <>
          <h2>Бэктест: работает ли скор</h2>
          <p style={{ color: "var(--muted)" }}>
            Скор считается по состоянию на дату (as-of, без знания будущего) и сверяется с тем, что
            монеты показали за следующие {backtest.params.horizon_days} дн. Периодов:{" "}
            {backtest.summary.periods}, с {backtest.params.start}.
          </p>

          <div className="stat-row">
            <div className="stat">
              <div className="label">Верх минус низ лесенки</div>
              <div
                className="value"
                style={{
                  color:
                    (backtest.summary.mean_long_short ?? 0) > 0 ? "var(--good)" : "var(--critical)",
                }}
              >
                {pct(backtest.summary.mean_long_short)}
              </div>
              <div className="hint">
                за период; плюс в {pct(backtest.summary.long_short_positive_rate, 0)} периодов —
                качество ранжирования, не зависит от фазы рынка
              </div>
            </div>
            <div className="stat">
              <div className="label">Spearman (ранг vs доходность)</div>
              <div className="value">{backtest.summary.mean_spearman ?? "н/д"}</div>
              <div className="hint">
                медиана {backtest.summary.median_spearman ?? "н/д"}; плюс в{" "}
                {pct(backtest.summary.spearman_positive_rate, 0)} периодов (0 = скор бесполезен)
              </div>
            </div>
            <div className="stat">
              <div className="label">Топ-3 против HODL BTC</div>
              <div
                className="value"
                style={{
                  color:
                    backtest.summary.strategy_cum > backtest.summary.btc_cum
                      ? "var(--good)"
                      : "var(--critical)",
                }}
              >
                {pct(backtest.summary.strategy_cum, 0)}
              </div>
              <div className="hint">
                против {pct(backtest.summary.btc_cum, 0)} у биткоина; топ обгонял BTC в{" "}
                {pct(backtest.summary.hit_rate, 0)} периодов
              </div>
            </div>
          </div>

          <div className="alert warn">
            <b>Как это читать.</b> Скор ранжирует альты в нужную сторону (верх обгоняет низ), но
            ротация в альты всё равно проигрывала простому удержанию биткоина: 2024–2026 были
            «сезоном биткоина». Лесенка отвечает на вопрос «в какую монету», а не «пора ли вообще
            выходить из BTC» — на второй отвечают ворота входа на главной.
          </div>

          <div className="card" style={{ overflowX: "auto" }}>
            <table className="data">
              <thead>
                <tr>
                  <th>Дата среза</th>
                  <th className="num">Монет</th>
                  <th className="num">Покрытие</th>
                  <th className="num">Spearman</th>
                  <th className="num">Топ vs BTC</th>
                  <th className="num">Низ vs BTC</th>
                  <th className="num">BTC за период</th>
                  <th>Топ-3 на дату</th>
                </tr>
              </thead>
              <tbody>
                {[...backtest.dates].reverse().map((d) => (
                  <tr key={d.date}>
                    <td>{d.date}</td>
                    <td className="num">{d.n}</td>
                    <td className="num">{pct(d.mean_coverage, 0)}</td>
                    <td className="num">{d.spearman ?? "—"}</td>
                    <td
                      className="num"
                      style={{ color: d.top_excess > 0 ? "var(--good)" : "var(--critical)" }}
                    >
                      {pct(d.top_excess)}
                    </td>
                    <td className="num">{pct(d.bottom_excess)}</td>
                    <td className="num">{pct(d.btc_return)}</td>
                    <td>{d.top_symbols.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p style={{ color: "var(--muted)", fontSize: 12 }}>
            Ограничения: {backtest.summary.caveats.join(" ")}
          </p>
        </>
      )}
    </>
  );
}
