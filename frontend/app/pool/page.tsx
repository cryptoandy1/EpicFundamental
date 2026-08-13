"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ProjectSummary, ScreenerResponse } from "@/lib/api";
import { fmtUsd } from "@/lib/theme";

export default function PoolPage() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [screener, setScreener] = useState<ScreenerResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<ProjectSummary[]>("/api/projects").then(setProjects).catch((e) => setError(String(e)));
    api<ScreenerResponse>("/api/screener").then(setScreener).catch(() => null);
  }, []);

  if (error) return <div className="alert danger">API недоступен: {error}</div>;
  if (!projects) return <div className="empty">Загрузка…</div>;

  return (
    <>
      <h2>Утверждённый пул</h2>
      <div className="card">
        <table className="data">
          <thead>
            <tr>
              <th>Монета</th>
              <th>Сеть</th>
              <th className="num">Цена</th>
              <th className="num">Капитализация</th>
              <th className="num">Скор лесенки</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {projects.map((p) => (
              <tr key={p.id}>
                <td>
                  <b>{p.symbol}</b> <span style={{ color: "var(--muted)" }}>{p.name}</span>
                </td>
                <td>{p.chain}</td>
                <td className="num">{p.price_usd !== null ? `$${p.price_usd.toLocaleString()}` : "—"}</td>
                <td className="num">{p.market_cap !== null ? fmtUsd(p.market_cap) : "—"}</td>
                <td className="num">{p.score !== null ? p.score.toFixed(1) : "—"}</td>
                <td>
                  <Link href={`/project/${p.id}`}>дашборд →</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {projects.length === 0 && <div className="empty">Пул пуст — добавьте монеты в config/projects.yaml</div>}
      </div>

      <h2>Кандидаты скринера (ф.3)</h2>
      <p style={{ color: "var(--muted)" }}>
        {screener?.snapshot_date
          ? `Снапшот ${screener.snapshot_date.slice(0, 10)}. Утверждаете кандидата — переносите в config/projects.yaml.`
          : "Скринер ещё не запускался: python -m app screen"}
      </p>
      {screener && screener.candidates.length > 0 && (
        <div className="card">
          <table className="data">
            <thead>
              <tr>
                <th>Монета</th>
                <th className="num">Капитализация</th>
                <th className="num">FDV</th>
                <th className="num">Объём 24ч</th>
                <th>Категории</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {screener.candidates.map((c) => (
                <tr key={c.coingecko_id}>
                  <td>
                    <b>{c.symbol}</b> <span style={{ color: "var(--muted)" }}>{c.name}</span>
                  </td>
                  <td className="num">{fmtUsd(c.market_cap)}</td>
                  <td className="num">{c.fdv ? fmtUsd(c.fdv) : "—"}</td>
                  <td className="num">{fmtUsd(c.volume_24h)}</td>
                  <td>{c.categories}</td>
                  <td>{c.in_pool ? <span className="tag good">в пуле</span> : <span className="tag">кандидат</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
