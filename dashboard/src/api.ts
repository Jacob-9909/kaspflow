/**
 * 백엔드 호출과 응답 타입. 모든 요청은 같은 출처의 /api 로 간다 (Caddy·nginx·vite 가 백엔드로 넘긴다).
 * 결과(Result) 타입은 backend/pipeline.py 의 result dict 와 맞춘다.
 */
export type Verdict = "buy" | "hold" | "sell";
export type Horizon = "short" | "mid" | "long";
export type Candle = [number, number, number, number, number, number]; // t, o, h, l, c, v
export type Point = [number, number];

export interface SearchHit { symbol: string; name: string; type: string; exchange: string }

export interface Quote {
  symbol: string; name: string; type: string; exchange: string | null; sector: string | null; industry: string | null;
  currency: string; market_cap: number | null; price: number; change: number; high52: number; low52: number;
  as_of: string; summary: string; candles: Candle[]; sma50: Point[]; sma200: Point[];
}

export interface VerdictInfo {
  horizon: Horizon; verdict: Verdict; label: string; strong: boolean; score: number; confidence: number;
  agreement: number; coverage: number; contributions: Record<string, number>; weights: Record<string, number>; notes: string[];
}

export interface ModuleCard { key: string; label: string; available: boolean; score: number; confidence: number; grade: string; summary: string }

export interface Metrics {
  total_return: number | null; cagr: number | null; vol: number | null; sharpe: number | null; sortino: number | null;
  max_drawdown: number | null; calmar: number | null; trades: number; win_rate: number | null; profit_factor: number | null;
  avg_win: number | null; avg_loss: number | null; avg_days: number | null; exposure: number | null;
}

export interface Marker { time: number; side: "buy" | "sell"; price: number; ret?: number }

export interface StrategyResult {
  key: string; label: string; family: "trend" | "reversion" | "day"; origin: string; rule: string; variants: number;
  best_full: { params: Record<string, number>; metrics: Metrics };
  wf: null | { folds: { oos_start: string; oos_end: string; params: Record<string, number>; is_sharpe: number; oos_sharpe: number }[];
    metrics: Metrics; efficiency: number | null; equity: Point[]; bh_equity: Point[] };
  perm_p: number | null; dsr: number | null;
  mc: null | { ret_p5: number; ret_p50: number; ret_p95: number; mdd_p5: number; mdd_p50: number };
  grade: string; weight: number;
  current: { params: Record<string, number>; decision: number; fresh: boolean; since_days: number | null; vote: number;
    levels: Record<string, number | null> };
  markers: Marker[];
}

export interface Plan {
  entry: number; stop: number; target: number; stop_pct: number; target_pct: number; reward_risk: number | null;
  p_target_first: number; p_stop_first: number; p_neither: number; expected_return: number; atr: number;
  atr_mult: [number, number]; max_weight: number | null; kelly_half: number | null;
}

export interface HorizonSim { days: number; p_up: number; median: number; p5: number; p25: number; p75: number; p95: number; cvar95: number }

export interface Fan { days: number[]; p5: number[]; p25: number[]; p50: number[]; p75: number[]; p95: number[] }

export interface Analog {
  available: boolean; score: number; confidence: number; summary: string; state?: string; horizon_days?: number; n?: number;
  n_eff?: number; mean?: number; median?: number; hit_rate?: number; base_mean?: number; base_hit_rate?: number; t_stat?: number;
  hist?: { counts: number[]; edges: number[] };
}

export interface Check { name: string; pass: boolean | null; value?: string | null }

export interface Persona {
  name: string; philosophy: string; checklist: Check[]; rule_score: number | null; score: number | null; error: string | null;
  view: null | { signal: "bullish" | "neutral" | "bearish"; confidence: number; thesis: string; positives: string[]; negatives: string[] };
}

export interface NewsItem {
  id: string; title: string; source: string; url: string; published: string; summary?: string;
  relevance?: number; sentiment?: number; impact?: string; event?: string; title_ko?: string;
}

export interface FieldRow { method: string; low: number; mid: number; high: number }

export interface Result {
  version: number; symbol: string; name: string; horizon: Horizon; as_of: string; ppy: number; run_id?: string; created_at?: string;
  position: { qty: number; avg_price: number } | null;
  quote: { symbol: string; name: string; price: number; change: number; currency: string; exchange: string | null; type: string;
    sector: string | null; industry: string | null; market_cap: number | null; as_of: string; partial_bar: boolean };
  verdict: VerdictInfo; verdicts: Record<Horizon, VerdictInfo>;
  advice: { holding: boolean; pnl: number | null; text: string };
  narrative: { headline: string; summary: string; bull: string[]; bear: string[]; risks: string[]; triggers: string[] };
  modules: ModuleCard[];
  technical: { summary: string; levels: Record<string, number | null>; detail: Record<string, number | boolean | null> };
  regime: { summary: string; available: boolean; score: number; detail: Record<string, unknown> };
  risk: { vol_1y: number; beta: number | null; mdd_1y: number; mdd_5y: number; var95_1d: number; atr: number; atr_pct: number; level: string };
  analogs: Record<Horizon, Analog>;
  validation: { strategies: StrategyResult[]; n_trials: number; sr0_annual: number; folds: number; bars: number; start: string; end: string;
    buy_hold: { full: Metrics; oos: Metrics | null } };
  simulation: { summary: Record<Horizon, HorizonSim> & { fan: Fan }; plans: Record<Horizon, Plan>; drift: number; paths: number };
  fundamentals: {
    facts: Record<string, string>;
    quality: { available: boolean; score: number; summary: string; groups: Record<string, number>; altman_z: number | null;
      piotroski: null | { score: number; max: number; checks: Check[] } };
    valuation: { available: boolean; score: number; summary: string; fair_value: number | null; upside: number | null;
      football_field: FieldRow[]; models?: Record<string, number>; notes?: string[]; multiples?: Record<string, number | null>;
      dcf: null | { per_share: number; growth: number; growth_sources: string[]; wacc: number; cost_of_equity: number; beta: number;
        terminal_growth: number; tv_share: number | null; implied_growth: number;
        sensitivity: { wacc: number[]; g_term: number[]; values: number[][] } } };
    consensus: { available: boolean; score: number; summary: string; detail?: Record<string, unknown>;
      recent?: { date: string; firm: string; to: string; from: string; action: string; target: number | null }[] };
  };
  news: { aggregate: { available: boolean; score: number; summary: string; drivers?: { title: string; sentiment: number; event: string; date: string }[];
    events?: Record<string, number>; recent_7d?: number | null; older?: number | null }; items: NewsItem[]; error: string | null };
  personas: { available: boolean; score: number; summary: string; members: Record<string, Persona>; error?: string };
  chart: { candles: Candle[]; sma50: Point[]; sma200: Point[] };
  meta: { duration_ms: number; llm_model: string | null; llm_calls: number | null; narrative_error: string | null; disclaimer: string };
}

export interface RunRow {
  id: string; symbol: string; name: string; created_at: string; horizon: Horizon; verdict: Verdict; score: number; confidence: number;
  price: number; headline: string | null; due: string; evaluated: boolean; return: number | null; hit: boolean | null; current: number | null;
  position?: { qty: number; avg_price: number } | null;
  source?: string | null;   // "auto" 면 관심 종목 자동 판단
}

export interface WatchRun { id: string; created_at: string; verdict: Verdict; score: number }
export interface WatchItem { symbol: string; horizon: Horizon; added_at: string; name: string | null; last: WatchRun | null; prev: WatchRun | null }
export interface WatchResponse { items: WatchItem[]; next_run: string; max: number }

export interface RunsResponse {
  runs: RunRow[];
  stats: { evaluated: number; hit_rate: number | null; by_verdict: Partial<Record<Verdict, { n: number; hit_rate: number; avg_return: number }>> };
}

export interface Me { authenticated: boolean; login_enabled: boolean; llm: boolean }

export type StreamEvent =
  | { type: "start"; steps: [string, string][]; queued: boolean }
  | { type: "step"; step: string; status: "running" | "done"; detail?: string; ms?: number }
  | { type: "done"; run_id: string | null; data: Result }
  | { type: "error"; message: string };

async function getJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, { credentials: "same-origin", ...init });
  if (!r.ok) {
    let msg = `${r.status}`;
    try {
      const body = await r.json();
      msg = typeof body.detail === "string" ? body.detail : msg;
    } catch { /* 본문이 JSON 이 아니면 상태 코드만 */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export const api = {
  me: () => getJSON<Me>("/auth/me"),
  login: (password: string) => getJSON<{ ok: boolean }>("/auth/login", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }) }),
  logout: () => getJSON<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  search: (q: string) => getJSON<SearchHit[]>(`/search?q=${encodeURIComponent(q)}`),
  quote: (symbol: string) => getJSON<Quote>(`/quote/${encodeURIComponent(symbol)}`),
  runs: (symbol?: string) => getJSON<RunsResponse>(`/runs${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`),
  run: (id: string) => getJSON<Result>(`/runs/${encodeURIComponent(id)}`),
  watchlist: () => getJSON<WatchResponse>("/watchlist"),
  watchAdd: (symbol: string, horizon: Horizon) => getJSON<{ ok: boolean }>("/watchlist", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol, horizon }) }),
  watchRemove: (symbol: string) => getJSON<{ ok: boolean }>(`/watchlist/${encodeURIComponent(symbol)}`, { method: "DELETE" }),
};

/** 검증·시뮬레이션 실행. EventSource 는 같은 출처 쿠키를 자동으로 보낸다. 반환값으로 연결을 끊는다. */
export function analyze(symbol: string, horizon: Horizon, position: { qty: number; avg: number } | null,
                        onEvent: (e: StreamEvent) => void): () => void {
  const q = new URLSearchParams({ horizon });
  if (position) {
    q.set("qty", String(position.qty));
    q.set("avg", String(position.avg));
  }
  const es = new EventSource(`/api/analyze/${encodeURIComponent(symbol)}?${q}`);
  let finished = false;
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data) as StreamEvent;
    if (ev.type === "done" || ev.type === "error") {
      finished = true;
      es.close();
    }
    onEvent(ev);
  };
  es.onerror = () => {
    if (!finished) onEvent({ type: "error", message: "서버와 연결이 끊겼습니다. 분석은 서버에서 계속되며 끝나면 기록에 저장됩니다." });
    es.close();
  };
  return () => es.close();
}
