"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, LadderRow } from "@/lib/api";

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
};

export default function LadderPage() {
  const [rows, setRows] = useState<LadderRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<LadderRow[]>("/api/ladder").then(setRows).catch((e) => setError(String(e)));
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
    </>
  );
}
