/** 숫자·날짜 표기와 판단 라벨. 색은 한국 증권 관례: 상승·매수 = 빨강(--up), 하락·매도 = 파랑(--down). */
import type { Horizon, Verdict } from "./api";

const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);

export function pct(x: number | null | undefined, digits = 1, sign = true): string {
  if (!isNum(x)) return "-";
  const v = (x * 100).toFixed(digits);
  return `${sign && x > 0 ? "+" : ""}${v}%`;
}

export function usd(x: number | null | undefined): string {
  if (!isNum(x)) return "-";
  const digits = Math.abs(x) < 1 ? 4 : 2;
  return `$${x.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function num(x: number | null | undefined, digits = 2): string {
  if (!isNum(x)) return "-";
  return x.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function big(x: number | null | undefined): string {
  if (!isNum(x)) return "-";
  const a = Math.abs(x);
  for (const [d, u] of [[1e12, "T"], [1e9, "B"], [1e6, "M"]] as const) if (a >= d) return `$${(x / d).toFixed(2)}${u}`;
  return `$${x.toFixed(0)}`;
}

export function date(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, "0")}.${String(d.getDate()).padStart(2, "0")}`;
}

export function dateTime(iso: string): string {
  const d = new Date(iso);
  return `${date(iso)} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export const VERDICT_LABEL: Record<Verdict, string> = { buy: "매수", hold: "관망", sell: "매도" };
export const HORIZON_LABEL: Record<Horizon, string> = { short: "단기 1개월", mid: "중기 3개월", long: "장기 1년" };
export const HORIZON_SHORT: Record<Horizon, string> = { short: "1개월", mid: "3개월", long: "1년" };
export const HORIZONS: Horizon[] = ["short", "mid", "long"];

/** 점수(-1~+1)의 방향 클래스. 플러스는 매수 쪽(빨강), 마이너스는 매도 쪽(파랑). */
export const dirClass = (x: number | null | undefined, dead = 0.05) =>
  !isNum(x) ? "flat" : x > dead ? "up" : x < -dead ? "down" : "flat";

export const signed = (x: number | null | undefined, digits = 2) => (!isNum(x) ? "-" : `${x > 0 ? "+" : ""}${x.toFixed(digits)}`);

/** 모듈 점수(-1~+1)를 말로 옮긴다. 경계는 판단 엔진과 같다: ±0.15 를 넘으면 한쪽 근거, ±0.4 를 넘으면 강한 근거.
 *  (예전 A~F 글자 등급은 '매수에 유리한 정도' 였는데 전략 검증 등급과 글자가 같아 뜻이 헷갈렸다) */
export function stance(score: number, available = true): { label: string; cls: "up" | "down" | "flat" } {
  if (!available) return { label: "데이터 없음", cls: "flat" };
  if (score >= 0.4) return { label: "강한 매수 근거", cls: "up" };
  if (score >= 0.15) return { label: "매수 근거", cls: "up" };
  if (score <= -0.4) return { label: "강한 매도 근거", cls: "down" };
  if (score <= -0.15) return { label: "매도 근거", cls: "down" };
  return { label: "중립", cls: "flat" };
}

/** 전략 검증 등급: 백테스트 성과가 우연이 아닌지 매긴다 (backend/validation.py GRADES). [등급, 뜻, 판단 반영] */
export const GRADE_INFO: [string, string, string][] = [
  ["A", "믿을 만함", "100% 반영"], ["B", "대체로 믿을 만함", "60% 반영"], ["C", "근거가 약함", "25% 반영"], ["F", "우연과 구별되지 않음", "반영 안 함"],
];

/** 근거 일치도(0~1)를 말로 */
export const agreementWord = (a: number) => (a >= 0.6 ? "한쪽으로 모임" : a >= 0.3 ? "보통" : "엇갈림");

/** Yahoo Finance 섹터 이름 (11개) */
export const SECTOR_KO: Record<string, string> = {
  Technology: "기술", Healthcare: "헬스케어", "Financial Services": "금융", "Consumer Cyclical": "경기소비재",
  "Consumer Defensive": "필수소비재", "Communication Services": "커뮤니케이션", Industrials: "산업재", Energy: "에너지",
  Utilities: "유틸리티", "Real Estate": "부동산", "Basic Materials": "소재",
};
