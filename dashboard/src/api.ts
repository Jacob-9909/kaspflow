/**
 * API 클라이언트: 백엔드(FastAPI)와 통신하는 함수들
 * ============================================================
 * [이 파일이 하는 일]
 *   백엔드의 /symbols, /ohlc 엔드포인트를 호출해 결과를 타입 안전하게 돌려준다.
 *
 * [경로 규칙]
 *   모든 요청은 "/api" 접두사를 붙인다.
 *   - 개발(vite dev): vite.config.ts 프록시가 /api -> localhost:8000 으로 전달
 *   - 배포(nginx):    nginx가 /api -> backend:8000 으로 프록시
 *   덕분에 브라우저에서는 항상 같은 출처라 CORS 문제가 없다.
 *
 * [데이터 흐름에서의 위치]
 *   ... -> Postgres -> FastAPI -> [이 api.ts] -> React 컴포넌트 -> 차트
 */

// 백엔드 /ohlc 가 돌려주는 캔들 1개의 형태 (init.sql의 ohlc_1m 컬럼과 동일)
export interface Candle {
  symbol: string;
  window_start: string; // ISO8601 문자열 (예: "2026-09-25T07:44:00Z")
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  trade_count: number;
}

// /ohlc 응답 전체 형태
interface OhlcResponse {
  symbol: string;
  count: number;
  earliest?: string | null; // 저장된 가장 오래된 버킷의 시작 시각 (과거 끝 판단용, 구버전 백엔드엔 없음)
  candles: Candle[];
}

/** fetchOhlc 결과: 봉 + 저장소의 가장 오래된 버킷 시각 */
export interface OhlcPage {
  candles: Candle[];
  earliest: string | null;
}

// /symbols 응답 형태
interface SymbolsResponse {
  symbols: string[];
}

/** 공통 fetch 헬퍼: 실패하면 에러를 던진다. (백엔드가 detail 을 주면 그 메시지를 사용) */
async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    let detail = "";
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* JSON 이 아니면 무시 */
    }
    throw new Error(detail || `요청 실패 (${res.status}): ${url}`);
  }
  return (await res.json()) as T;
}

/** 저장된 심볼 목록을 가져온다. */
export async function fetchSymbols(): Promise<string[]> {
  const data = await getJson<SymbolsResponse>("/api/symbols");
  return data.symbols;
}

/**
 * 특정 심볼의 봉을 시간 오름차순으로 가져온다. interval 기본 1m.
 * before 를 주면 그 시각보다 이전의 봉만 받는다(왼쪽으로 스크롤할 때 과거를 이어 붙이는 용도).
 */
export async function fetchOhlcPage(
  symbol: string,
  interval = "1m",
  limit = 120,
  before?: string,
): Promise<OhlcPage> {
  const params = new URLSearchParams({ symbol, interval, limit: String(limit) });
  if (before) params.set("before", before);
  const data = await getJson<OhlcResponse>(`/api/ohlc?${params.toString()}`);
  return { candles: data.candles, earliest: data.earliest ?? null };
}

/** 지원하는 봉 간격 목록 (예: ["1m","5m","15m","1h","4h","1d"]). */
export async function fetchIntervals(): Promise<string[]> {
  try {
    const data = await getJson<{ intervals: string[] }>("/api/intervals");
    return data.intervals;
  } catch {
    // 백엔드가 구버전이면 최소한 1m 은 되도록 폴백
    return ["1m"];
  }
}

// ============================================================
// 백테스트 (midas-touch 전략 이식, 백엔드 backtest.py)
// ============================================================

/** 전략 정보: /backtest/strategies 의 항목 */
export interface StrategyInfo {
  id: string;
  label: string;
  params: Record<string, number>; // 기본 파라미터 (입력 폼의 초기값)
}

/** 백테스트 실행 설정 (UI 입력값). 손절/익절/추격은 비율(0.02 = 2%), null = 미사용 */
export interface BacktestConfig {
  strategy: string;
  limit: number; // 백테스트에 쓸 최근 봉 수 (차트도 같은 수를 불러온다)
  initialCapital: number;
  feeBps: number;
  stopLossPct: number | null;
  takeProfitPct: number | null;
  trailingStopPct: number | null;
  params: Record<string, number>;
}

/** 차트에 찍을 매수/매도 이벤트. time 은 lightweight-charts 와 같은 UTC epoch 초 */
export interface BacktestMarker {
  time: number;
  side: "buy" | "sell";
  price: number;
  reason: string; // signal | stop_loss | take_profit | trailing_stop
  pnl_pct: number | null; // 매도 시 손익(수수료 반영), 매수는 null
}

export interface BacktestTrade {
  entry_time: number;
  exit_time: number;
  entry_price: number;
  exit_price: number;
  qty: number;
  pnl_pct: number;
  pnl_amount: number;
  exit_reason: string;
  bars_held: number;
}

export interface BacktestMetrics {
  total_return: number;
  buy_hold_return: number;
  max_drawdown: number;
  final_value: number;
  trade_count: number;
  win_rate: number;
  profit_factor: number;
  avg_win_pct: number;
  avg_loss_pct: number;
  sharpe_ratio: number;
  exposure_pct: number;
  exit_reasons: Record<string, number>;
}

export interface BacktestResult {
  symbol: string;
  interval: string;
  strategy: string;
  label: string;
  bars: number;
  initial_capital: number;
  params_used: Record<string, number>;
  metrics: BacktestMetrics;
  trades: BacktestTrade[];
  markers: BacktestMarker[];
  open_position: {
    entry_time: number;
    entry_price: number;
    qty: number;
    unrealized_pct: number;
  } | null;
  warnings: string[];
}

interface StrategiesResponse {
  strategies: StrategyInfo[];
  risk_defaults: { fee_bps: number };
}

/** 전략 목록 + 기본 리스크 설정. 구버전 백엔드(미지원)면 null -> 패널을 숨긴다. */
export async function fetchBacktestStrategies(): Promise<StrategiesResponse | null> {
  try {
    return await getJson<StrategiesResponse>("/api/backtest/strategies");
  } catch {
    return null;
  }
}

/** 선택한 봉 간격(interval)으로 백테스트를 실행한다. */
export async function fetchBacktest(
  symbol: string,
  interval: string,
  cfg: BacktestConfig,
): Promise<BacktestResult> {
  const q = new URLSearchParams({
    symbol,
    interval,
    strategy: cfg.strategy,
    limit: String(cfg.limit),
    initial_capital: String(cfg.initialCapital),
    fee_bps: String(cfg.feeBps),
  });
  if (cfg.stopLossPct !== null) q.set("stop_loss_pct", String(cfg.stopLossPct));
  if (cfg.takeProfitPct !== null) q.set("take_profit_pct", String(cfg.takeProfitPct));
  if (cfg.trailingStopPct !== null) q.set("trailing_stop_pct", String(cfg.trailingStopPct));
  if (Object.keys(cfg.params).length > 0) q.set("params", JSON.stringify(cfg.params));
  return getJson<BacktestResult>(`/api/backtest?${q.toString()}`);
}
