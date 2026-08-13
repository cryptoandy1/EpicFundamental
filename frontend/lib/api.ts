// Типы ответов FastAPI + fetch-помощник.
export type Point = [string, number];

export interface MarketOverview {
  btc_price: Point[];
  btc_trends_monthly: Point[];
  btc_trends_weekly: Point[];
  trends_percentile: number | null;
  sell_signal: boolean;
  coinbase_rank_overall: Point[];
  coinbase_rank_finance: Point[];
}

export interface ProjectSummary {
  id: string;
  name: string;
  symbol: string;
  chain: string;
  genesis_date: string | null;
  price_usd: number | null;
  market_cap: number | null;
  score: number | null;
}

export interface EventPoint {
  ts: string;
  title: string;
  value: number;
  meta: Record<string, unknown> | null;
}

export interface ProjectDetail {
  project: {
    id: string;
    name: string;
    symbol: string;
    chain: string;
    genesis_date: string | null;
    twitter_project: string;
    twitter_ceo: string;
    github_repos: string[];
    notes: string;
  };
  price: Point[];
  market_cap: Point[];
  unlocked_total: Point[];
  unlock_events: EventPoint[];
  funding_events: EventPoint[];
  github_commits_week: Point[];
  github_peers: Record<string, Point[]>;
  trends_weekly: Point[];
  trends_monthly: Point[];
  media_mentions: Point[];
  recent_mentions: { ts: string; title: string; url: string; domain: string; is_pr: boolean }[];
  node_count: Point[];
  exchange_validators: Point[];
  validator_events: EventPoint[];
  team_outflow_tokens: Point[];
  team_to_exchange_tokens: Point[];
  top_outflows: {
    ts: string;
    amount: number;
    token: string;
    to_exchange: boolean;
    counterparty: string;
    wallet: string;
    tx_hash: string;
  }[];
  smart_money_fresh_share: Point[];
  discord_messages_week: Point[];
  discord_substantive_week: Point[];
  ceo_tweets_week: Point[];
  twitter_curation: { handle: string; stance: string; note?: string }[];
}

export interface ScreenerResponse {
  snapshot_date: string | null;
  candidates: {
    coingecko_id: string;
    name: string;
    symbol: string;
    market_cap: number;
    fdv: number;
    volume_24h: number;
    categories: string;
    reason: string;
    in_pool: boolean;
  }[];
}

export interface LadderRow {
  project: string;
  name: string;
  symbol: string;
  score: number | null;
  factors: Record<string, { value: number | null; percentile: number | null; weight: number }>;
}

// Статическая сборка (GitHub Pages): вместо живого API читаем JSON-снапшоты,
// которые кладёт `python -m app export` в public/data (та же структура путей).
const STATIC = process.env.NEXT_PUBLIC_STATIC === "1";
const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export async function api<T>(path: string): Promise<T> {
  const url = STATIC ? `${BASE}/data${path.replace(/^\/api/, "")}.json` : path;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}
