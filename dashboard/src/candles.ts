/**
 * 캔들 배열 병합 유틸 (순수 함수)
 * ============================================================
 * [왜 필요한가]
 *   차트가 "최근 N봉 통째 교체" 방식이면 과거를 불러와도 5초 폴링에 덮여 사라진다.
 *   그래서 한 배열(시간 오름차순)을 유지하고,
 *     - 5초 폴링으로 받은 '최신 구간(tail)'은 뒤쪽에 덮어쓰고 (mergeTail)
 *     - 왼쪽 스크롤로 받은 '과거 구간'은 앞쪽에 이어 붙인다 (prependOlder).
 */
import type { Candle } from "./api";

/** 봉 간격(문자열) -> 밀리초. 모르는 값이면 null. */
const STEP_MS: Record<string, number> = {
  "1m": 60_000,
  "5m": 5 * 60_000,
  "15m": 15 * 60_000,
  "1h": 60 * 60_000,
  "4h": 4 * 60 * 60_000,
  "1d": 24 * 60 * 60_000,
};

export function stepMs(interval: string): number | null {
  return STEP_MS[interval] ?? null;
}

const time = (c: Candle) => Date.parse(c.window_start);

/**
 * 최신 구간(tail)을 기존 배열 뒤에 합친다.
 * - tail 의 첫 봉 시각 이후의 기존 봉은 전부 tail 의 값으로 교체한다(진행 중인 마지막 봉 갱신 포함).
 * - tail 이 기존 마지막 봉과 이어지지 않고 떨어져 있으면(탭이 오래 멈췄던 경우 등) 중간이 비므로
 *   null 을 돌려준다. 호출자는 이때 tail 로 새로 시작해야 한다.
 */
export function mergeTail(existing: Candle[], tail: Candle[], step: number | null): Candle[] | null {
  if (tail.length === 0) return existing;
  if (existing.length === 0) return tail;

  const tailStart = time(tail[0]);
  const lastExisting = time(existing[existing.length - 1]);
  if (step !== null && tailStart > lastExisting + step) return null; // 사이가 비었다

  // existing 에서 tailStart 이상인 첫 위치(이진 탐색)
  let lo = 0;
  let hi = existing.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (time(existing[mid]) < tailStart) lo = mid + 1;
    else hi = mid;
  }
  return existing.slice(0, lo).concat(tail);
}

/**
 * 과거 구간(older)을 기존 배열 앞에 이어 붙인다.
 * 서버가 before 로 걸러 주지만, 방어적으로 기존 첫 봉 이상인 봉은 버려 중복/역전을 막는다.
 */
export function prependOlder(existing: Candle[], older: Candle[]): Candle[] {
  if (older.length === 0) return existing;
  if (existing.length === 0) return older;
  const firstExisting = time(existing[0]);
  const fresh = older.filter((c) => time(c) < firstExisting);
  return fresh.length === 0 ? existing : fresh.concat(existing);
}
