"use client";
import { useEffect, useMemo, useState } from "react";
import Chart from "@/components/Chart";
import { api, EventPoint, Point, ProjectDetail } from "@/lib/api";
import { TOKENS, Tokens, baseOption, barSeries, fmtNum, fmtUsd, lineSeries, useMode } from "@/lib/theme";

/** Вертикальные отметки событий (разлоки, раунды, валидаторы) поверх графика. */
function eventMarks(events: EventPoint[], color: string, formatter: (e: EventPoint) => string) {
  return {
    silent: false,
    symbol: "none",
    lineStyle: { color, type: "dashed" as const, width: 1 },
    label: { show: false },
    data: events.map((e) => ({ xAxis: e.ts, name: formatter(e) })),
    tooltip: { formatter: (p: { name: string }) => p.name },
  };
}

/** Дневные точки -> недельные суммы (для читаемости плотных рядов). */
function weekly(points: Point[]): Point[] {
  const acc = new Map<string, number>();
  for (const [iso, v] of points) {
    const d = new Date(iso);
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); // понедельник
    const key = d.toISOString().slice(0, 10);
    acc.set(key, (acc.get(key) ?? 0) + v);
  }
  return [...acc.entries()].sort(([a], [b]) => (a < b ? -1 : 1));
}

function useOptions(data: ProjectDetail | null, t: Tokens) {
  return useMemo(() => {
    if (!data) return null;
    const base = () => baseOption(t);

    // 1. Цена + оверлеи: разлоки (штрих критичного цвета) и фандинг
    const price = {
      ...base(),
      series: [
        {
          ...lineSeries(`${data.project.symbol}, $`, data.price),
          markLine: eventMarks(
            data.unlock_events,
            t.critical,
            (e) => `${e.ts.slice(0, 10)} — ${e.title} (${fmtNum(e.value)} токенов)`
          ),
        },
        {
          name: "Фандинг-раунды",
          type: "scatter" as const,
          symbolSize: 10,
          itemStyle: { color: t.series[2] },
          data: data.funding_events.map((e) => {
            const at = data.price.find(([ts]) => ts >= e.ts);
            return {
              value: [e.ts, at ? at[1] : null],
              name: `${e.ts.slice(0, 10)} — ${e.title}: ${fmtUsd(e.value)}`,
            };
          }),
          tooltip: { formatter: (p: { name: string }) => p.name },
        },
      ],
      yAxis: { ...base().yAxis, type: "log" as const },
    };

    // 2. Кумулятивная разблокированная эмиссия
    const unlocked = data.unlocked_total.length
      ? {
          ...base(),
          series: [
            {
              ...lineSeries("Разблокировано токенов", data.unlocked_total, {
                areaStyle: { opacity: 0.08 },
              }),
              markLine: {
                silent: true,
                symbol: "none",
                lineStyle: { color: t.muted, type: "dotted" as const },
                label: { formatter: "сегодня", color: t.muted, fontSize: 11 },
                data: [{ xAxis: new Date().toISOString() }],
              },
            },
          ],
        }
      : null;

    // 3. GitHub vs аналоги (проект + до 3 пиров = максимум 4 серии)
    const peerNames = Object.keys(data.github_peers).slice(0, 3);
    const github = data.github_commits_week.length
      ? {
          ...base(),
          series: [
            lineSeries(data.project.symbol, data.github_commits_week, { lineStyle: { width: 2.5 } }),
            ...peerNames.map((name) =>
              lineSeries(name, data.github_peers[name], { lineStyle: { width: 1.5, opacity: 0.8 } })
            ),
          ],
        }
      : null;

    // 4. Ноды + подключения биржевых валидаторов
    const nodes = data.node_count.length
      ? {
          ...base(),
          series: [
            {
              ...lineSeries("Валидаторы/ноды", data.node_count),
              markLine: eventMarks(
                data.validator_events,
                t.series[1],
                (e) => `${e.ts.slice(0, 10)} — ${e.title}`
              ),
            },
          ],
        }
      : null;

    // 5. Упоминания в СМИ (недельные)
    const mentions = data.media_mentions.length
      ? {
          ...base(),
          series: [barSeries("Статей в неделю (без PR-wire)", weekly(data.media_mentions))],
        }
      : null;

    // 6. Google Trends по проекту
    const trends = data.trends_weekly.length
      ? {
          ...base(),
          series: [lineSeries("Интерес (недели, 5 лет)", data.trends_weekly)],
          yAxis: { ...base().yAxis, max: 100 },
        }
      : null;

    // 7. Выводы кошельков команды: на биржи + прочие (стек = всего)
    const outflow = data.team_outflow_tokens.length
      ? (() => {
          const exchange = new Map(data.team_to_exchange_tokens);
          const other: Point[] = data.team_outflow_tokens.map(([ts, v]) => [
            ts,
            Math.max(0, v - (exchange.get(ts) ?? 0)),
          ]);
          return {
            ...base(),
            series: [
              barSeries("На биржи (вероятная продажа)", data.team_to_exchange_tokens, {
                stack: "out",
                itemStyle: { color: t.critical, borderRadius: [0, 0, 0, 0] },
              }),
              barSeries("Прочие выводы", other, { stack: "out" }),
            ],
          };
        })()
      : null;

    // 8. Discord: по существу + прочее (стек = всего сообщений)
    const discord = data.discord_messages_week.length
      ? (() => {
          const substantive = new Map(data.discord_substantive_week);
          const noise: Point[] = data.discord_messages_week.map(([ts, v]) => [
            ts,
            Math.max(0, v - (substantive.get(ts) ?? 0)),
          ]);
          return {
            ...base(),
            series: [
              barSeries("По существу", data.discord_substantive_week, { stack: "d" }),
              barSeries("gm/gn и прочий шум", noise, {
                stack: "d",
                itemStyle: { color: t.grid, borderRadius: [4, 4, 0, 0] },
              }),
            ],
          };
        })()
      : null;

    // 9. Активность CEO (ручной ввод)
    const ceo = data.ceo_tweets_week.length
      ? { ...base(), series: [lineSeries("Твитов CEO в неделю", data.ceo_tweets_week)] }
      : null;

    return { price, unlocked, github, nodes, mentions, trends, outflow, discord, ceo };
  }, [data, t]);
}

export default function ProjectClient({ id }: { id: string }) {
  const mode = useMode();
  const t = TOKENS[mode];
  const [data, setData] = useState<ProjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (id) api<ProjectDetail>(`/api/projects/${id}`).then(setData).catch((e) => setError(String(e)));
  }, [id]);

  const opts = useOptions(data, t);

  if (error) return <div className="alert danger">{error}</div>;
  if (!data || !opts) return <div className="empty">Загрузка…</div>;

  const p = data.project;
  const lastNodes = data.node_count.at(-1);
  const lastExchangeValidators = data.exchange_validators.at(-1);
  const lastSmart = data.smart_money_fresh_share.at(-1);
  const age = p.genesis_date
    ? `${((Date.now() - new Date(p.genesis_date).getTime()) / 31557600000).toFixed(1)} лет`
    : "н/д";

  const card = (title: string, sub: string, option: object | null, emptyHint: string) => (
    <div className="card">
      <h3>{title}</h3>
      <p className="sub">{sub}</p>
      {option ? <Chart option={option as never} /> : <div className="empty">{emptyHint}</div>}
    </div>
  );

  return (
    <>
      <h2>
        {p.name} <span style={{ color: "var(--muted)" }}>({p.symbol})</span>
      </h2>

      <div className="stat-row">
        <div className="stat">
          <div className="label">Возраст проекта (ф.4)</div>
          <div className="value">{age}</div>
          <div className="hint">genesis: {p.genesis_date?.slice(0, 10) ?? "н/д"}</div>
        </div>
        <div className="stat">
          <div className="label">Фандинг (ф.4)</div>
          <div className="value">
            {data.funding_events.length
              ? fmtUsd(data.funding_events.reduce((s, e) => s + e.value, 0))
              : "—"}
          </div>
          <div className="hint">
            {data.funding_events.length
              ? `${data.funding_events.length} раундов, последний ${data.funding_events.at(-1)!.ts.slice(0, 10)}`
              : "добавьте funding_rounds в projects.yaml"}
          </div>
        </div>
        <div className="stat">
          <div className="label">Валидаторы (ф.8/9)</div>
          <div className="value">{lastNodes ? fmtNum(lastNodes[1]) : "—"}</div>
          <div className="hint">
            {lastExchangeValidators ? `${lastExchangeValidators[1]} биржевых` : "биржевые не найдены"}
          </div>
        </div>
        <div className="stat">
          <div className="label">Smart money (ф.11)</div>
          <div className="value">{lastSmart ? `${(lastSmart[1] * 100).toFixed(0)}%` : "—"}</div>
          <div className="hint">доля объёма на свежие кошельки</div>
        </div>
      </div>

      <div className="grid2">
        {card(
          "Цена + разлоки + фандинг",
          "ф.7: штриховые линии — клифы разлоков (включая будущие); точки — раунды",
          opts.price,
          "нет цены — backfill market"
        )}
        {card(
          "Разблокированная эмиссия",
          "ф.7: кумулятивный график разлоков",
          opts.unlocked,
          "нужен DEFILLAMA_API_KEY или unlock_events в projects.yaml"
        )}
        {card(
          "GitHub-активность vs аналоги",
          "ф.5: коммиты в неделю, вся история (топ-100 контрибьюторов)",
          opts.github,
          "нет данных — backfill github (рекомендуется GITHUB_TOKEN)"
        )}
        {card(
          "Ноды / валидаторы",
          "ф.8–9: штриховые отметки — первое появление биржевого валидатора; история копится со временем",
          opts.nodes,
          "нет данных — backfill nodes"
        )}
        {card(
          "Упоминания в СМИ",
          "ф.10: GDELT без PR-wire площадок, с 2017",
          opts.mentions,
          "нет данных — backfill mentions"
        )}
        {card("Google Trends", "ф.14: интерес к проекту, недельные за 5 лет", opts.trends, "нет данных — backfill trends")}
        {card(
          "Выводы кошельков команды",
          "ф.7: токены в неделю; красное — на известные биржевые кошельки",
          opts.outflow,
          "заполните wallets в projects.yaml + ETHERSCAN_API_KEY"
        )}
        {card(
          "Discord-активность",
          "ф.12: сообщения по существу vs шум (классификация Claude API)",
          opts.discord,
          "укажите discord_export_dir (DiscordChatExporter)"
        )}
        {card(
          "Активность CEO в Twitter",
          `ф.13: @${p.twitter_ceo || "?"} — ручной ввод (без платного X API)`,
          opts.ceo,
          "заполните config/manual_metrics.yaml (ceo_tweets_week)"
        )}

        <div className="card">
          <h3>Авторитетные Twitter-аккаунты</h3>
          <p className="sub">ф.6: ручная курация — идейные vs платный шиллинг (projects.yaml → twitter_curation)</p>
          {data.twitter_curation.length ? (
            <table className="data">
              <thead>
                <tr>
                  <th>Аккаунт</th>
                  <th>Оценка</th>
                  <th>Комментарий</th>
                </tr>
              </thead>
              <tbody>
                {data.twitter_curation.map((a) => (
                  <tr key={a.handle}>
                    <td>
                      <a href={`https://x.com/${a.handle}`} target="_blank" rel="noreferrer">
                        @{a.handle}
                      </a>
                    </td>
                    <td>
                      {a.stance === "idea" ? (
                        <span className="tag good">идейный</span>
                      ) : (
                        <span className="tag bad">шиллер</span>
                      )}
                    </td>
                    <td>{a.note ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty">список пуст — заполните twitter_curation в projects.yaml</div>
          )}
        </div>
      </div>

      {data.top_outflows.length > 0 && (
        <div className="card">
          <h3>Крупнейшие выводы с кошельков команды</h3>
          <table className="data">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Кошелёк</th>
                <th className="num">Токенов</th>
                <th>Куда</th>
                <th>Tx</th>
              </tr>
            </thead>
            <tbody>
              {data.top_outflows.map((f) => (
                <tr key={f.tx_hash + f.ts}>
                  <td>{f.ts.slice(0, 10)}</td>
                  <td>{f.wallet}</td>
                  <td className="num">
                    {fmtNum(f.amount)} {f.token}
                  </td>
                  <td>{f.to_exchange ? <span className="tag bad">биржа</span> : f.counterparty.slice(0, 12) + "…"}</td>
                  <td>
                    <a href={`https://etherscan.io/tx/${f.tx_hash}`} target="_blank" rel="noreferrer">
                      ↗
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data.recent_mentions.length > 0 && (
        <div className="card">
          <h3>Свежие статьи (без PR-wire)</h3>
          <table className="data">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Заголовок</th>
                <th>Источник</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_mentions.map((m) => (
                <tr key={m.url}>
                  <td style={{ whiteSpace: "nowrap" }}>{m.ts.slice(0, 10)}</td>
                  <td>
                    <a href={m.url} target="_blank" rel="noreferrer">
                      {m.title || m.url.slice(0, 80)}
                    </a>
                  </td>
                  <td>{m.domain}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
