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
  candles: Candle[];
}

// /symbols 응답 형태
interface SymbolsResponse {
  symbols: string[];
}

/** 공통 fetch 헬퍼: 실패하면 에러를 던진다. */
async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`요청 실패 (${res.status}): ${url}`);
  }
  return (await res.json()) as T;
}

/** 저장된 심볼 목록을 가져온다. */
export async function fetchSymbols(): Promise<string[]> {
  const data = await getJson<SymbolsResponse>("/api/symbols");
  return data.symbols;
}

/** 특정 심볼의 최근 1분봉을 시간 오름차순으로 가져온다. */
export async function fetchOhlc(symbol: string, limit = 120): Promise<Candle[]> {
  const params = new URLSearchParams({ symbol, limit: String(limit) });
  const data = await getJson<OhlcResponse>(`/api/ohlc?${params.toString()}`);
  return data.candles;
}
