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
