/**
 * 차트 모음. 시계열(캔들·누적수익)은 TradingView lightweight-charts, 분포·범위 그림은 가벼운 인라인 SVG.
 * 색은 CSS 변수에서 읽는다 (테마가 바뀌면 theme prop 이 바뀌어 차트를 다시 만든다).
 */
import { useEffect, useRef } from "react";
import {
  ColorType, CrosshairMode, LineStyle, createChart,
  type SeriesMarker, type Time, type UTCTimestamp,
} from "lightweight-charts";
import type { Candle, Fan, FieldRow, Marker, Point } from "../api";
import { pct, usd } from "../format";

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function baseOptions() {
  return {
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--text-muted"),
      fontFamily: "Pretendard Variable, Pretendard, system-ui, sans-serif", fontSize: 11 },
    grid: { vertLines: { color: css("--grid") }, horzLines: { color: css("--grid") } },
    rightPriceScale: { borderColor: css("--border") },
    timeScale: { borderColor: css("--border") },
    crosshair: { mode: CrosshairMode.Normal },
  };
}

export interface PriceLevel { price: number; label: string; tone: "up" | "down" | "accent" }

export function PriceChart({ candles, sma50, sma200, markers, levels, theme, height = 380 }: {
  candles: Candle[]; sma50?: Point[]; sma200?: Point[]; markers?: Marker[]; levels?: PriceLevel[]; theme: string; height?: number;
}) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!el.current || candles.length === 0) return;
    const up = css("--up"), down = css("--down");
    const chart = createChart(el.current, baseOptions());
    const series = chart.addCandlestickSeries({ upColor: up, downColor: down, borderUpColor: up, borderDownColor: down,
      wickUpColor: up, wickDownColor: down });
    series.setData(candles.map(([t, o, h, l, c]) => ({ time: t as UTCTimestamp, open: o, high: h, low: l, close: c })));
    const vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false, priceLineVisible: false });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
    vol.setData(candles.map(([t, o, , , c, v]) => ({ time: t as UTCTimestamp, value: v, color: c >= o ? css("--up-weak") : css("--down-weak") })));
    const line = (pts: Point[] | undefined, color: string) => {
      if (!pts?.length) return;
      const s = chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(pts.map(([t, v]) => ({ time: t as UTCTimestamp, value: v })));
    };
    line(sma50, css("--sma50"));
    line(sma200, css("--sma200"));
    const times = new Set(candles.map((c) => c[0]));
    const mk: SeriesMarker<Time>[] = (markers ?? []).filter((m) => times.has(m.time)).sort((a, b) => a.time - b.time).map((m) =>
      m.side === "buy"
        ? { time: m.time as UTCTimestamp, position: "belowBar", shape: "arrowUp", color: up, text: "매수" }
        : { time: m.time as UTCTimestamp, position: "aboveBar", shape: "arrowDown", color: down, text: m.ret === undefined ? "매도" : pct(m.ret, 1) });
    series.setMarkers(mk);
    for (const lv of levels ?? []) {
      series.createPriceLine({ price: lv.price, color: css(lv.tone === "accent" ? "--accent" : `--${lv.tone}`), lineWidth: 1,
        lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: lv.label });
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [candles, sma50, sma200, markers, levels, theme]);
  return <div ref={el} className="chart" style={{ height }} role="img" aria-label="가격 캔들 차트" />;
}

export function EquityChart({ a, b, aLabel, bLabel, theme }: { a: Point[]; b: Point[]; aLabel: string; bLabel: string; theme: string }) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!el.current || a.length === 0) return;
    const chart = createChart(el.current, { ...baseOptions(), rightPriceScale: { borderColor: css("--border"), mode: 1 } });
    const add = (pts: Point[], color: string, title: string) => {
      const s = chart.addLineSeries({ color, lineWidth: 2, title, priceLineVisible: false, priceFormat: { type: "custom", formatter: (v: number) => pct(v - 1, 0) } });
      s.setData(pts.map(([t, v]) => ({ time: t as UTCTimestamp, value: v })));
    };
    add(b, css("--text-faint"), bLabel);
    add(a, css("--accent"), aLabel);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [a, b, aLabel, bLabel, theme]);
  return <div ref={el} className="chart" style={{ height: 240 }} role="img" aria-label={`${aLabel} 와 ${bLabel} 누적 수익 비교`} />;
}

/** 몬테카를로 팬 차트: 5~95% / 25~75% 띠 + 중앙값 + 현재가·손절·목표선 */
export function FanChart({ fan, days, price, stop, target }: { fan: Fan; days: number; price: number; stop?: number; target?: number }) {
  const W = 640, H = 240, P = { l: 8, r: 64, t: 12, b: 22 };
  const n = fan.days.findIndex((d) => d >= days);
  const k = n < 0 ? fan.days.length : n + 1;
  const xs = [0, ...fan.days.slice(0, k)];
  const series = (arr: number[]) => [price, ...arr.slice(0, k)];
  const p5 = series(fan.p5), p25 = series(fan.p25), p50 = series(fan.p50), p75 = series(fan.p75), p95 = series(fan.p95);
  const lo = Math.min(...p5, stop ?? Infinity) * 0.98, hi = Math.max(...p95, target ?? -Infinity) * 1.02;
  const x = (d: number) => P.l + (d / xs[xs.length - 1]) * (W - P.l - P.r);
  const y = (v: number) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
  const band = (top: number[], bot: number[]) =>
    top.map((v, i) => `${i ? "L" : "M"}${x(xs[i]).toFixed(1)},${y(v).toFixed(1)}`).join("") +
    bot.map((_, i) => { const j = bot.length - 1 - i; return `L${x(xs[j]).toFixed(1)},${y(bot[j]).toFixed(1)}`; }).join("") + "Z";
  const path = (arr: number[]) => arr.map((v, i) => `${i ? "L" : "M"}${x(xs[i]).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const hline = (v: number | undefined, cls: string, label: string) => v === undefined ? null : (
    <g className={cls}><line x1={P.l} x2={W - P.r} y1={y(v)} y2={y(v)} /><text x={W - P.r + 4} y={y(v) + 4}>{label}</text></g>);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="fan" role="img"
         aria-label={`${days}거래일 뒤 가격 분포. 중앙값 ${usd(p50[p50.length - 1])}, 90% 범위 ${usd(p5[p5.length - 1])} ~ ${usd(p95[p95.length - 1])}`}>
      <path d={band(p95, p5)} className="band-outer" />
      <path d={band(p75, p25)} className="band-inner" />
      <path d={path(p50)} className="median" />
      {hline(price, "lv-price", usd(price))}
      {hline(stop, "lv-stop", "손절")}
      {hline(target, "lv-target", "목표")}
      <text x={P.l} y={H - 6} className="axis">오늘</text>
      <text x={W - P.r} y={H - 6} className="axis" textAnchor="end">{days}거래일 뒤</text>
    </svg>
  );
}

/** 과거 유사 국면의 N일 뒤 수익률 분포 막대 */
export function Histogram({ counts, edges }: { counts: number[]; edges: number[] }) {
  const W = 640, H = 150, P = 18;
  const max = Math.max(...counts, 1);
  const bw = (W - 2 * P) / counts.length;
  const zero = P + ((0 - edges[0]) / (edges[edges.length - 1] - edges[0])) * (W - 2 * P);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="hist" role="img" aria-label="유사 국면 이후 수익률 분포">
      {counts.map((c, i) => {
        const h = (c / max) * (H - 2 * P);
        const mid = (edges[i] + edges[i + 1]) / 2;
        return <rect key={i} x={P + i * bw + 1} y={H - P - h} width={bw - 2} height={h} className={mid >= 0 ? "bar-up" : "bar-down"} />;
      })}
      <line x1={zero} x2={zero} y1={6} y2={H - P} className="zero" />
      <text x={P} y={H - 4} className="axis">{pct(edges[0], 0)}</text>
      <text x={zero} y={H - 4} className="axis" textAnchor="middle">0%</text>
      <text x={W - P} y={H - 4} className="axis" textAnchor="end">{pct(edges[edges.length - 1], 0)}</text>
    </svg>
  );
}

/** 반원 게이지 (-1 매도 ~ +1 매수) */
export function Gauge({ score, label }: { score: number; label: string }) {
  const r = 80, cx = 100, cy = 96;
  const ang = (s: number) => Math.PI * (1 - (Math.max(-1, Math.min(1, s)) + 1) / 2);
  const pt = (s: number, rr = r) => [cx + rr * Math.cos(ang(s)), cy - rr * Math.sin(ang(s))];
  const arc = (a: number, b: number) => { const [x1, y1] = pt(a), [x2, y2] = pt(b); return `M${x1},${y1} A${r},${r} 0 0 1 ${x2},${y2}`; };
  const [nx, ny] = pt(score, r - 14);
  return (
    <svg viewBox="0 0 200 112" className="gauge" role="img" aria-label={`종합 점수 ${Math.round(score * 100)} (${label})`}>
      <path d={arc(-1, -0.15)} className="g-down" />
      <path d={arc(-0.15, 0.15)} className="g-hold" />
      <path d={arc(0.15, 1)} className="g-up" />
      <line x1={cx} y1={cy} x2={nx} y2={ny} className="needle" />
      <circle cx={cx} cy={cy} r={5} className="hub" />
      <text x={22} y={110} className="axis">매도</text>
      <text x={178} y={110} className="axis" textAnchor="end">매수</text>
    </svg>
  );
}

/** 가운데 0 기준 막대 (-1~+1) */
export function ScoreBar({ score, muted = false }: { score: number; muted?: boolean }) {
  const s = Math.max(-1, Math.min(1, score));
  return (
    <div className={`scorebar${muted ? " muted" : ""}`} aria-hidden="true">
      <div className="mid" />
      <div className={`fill ${s >= 0 ? "up" : "down"}`} style={s >= 0 ? { left: "50%", width: `${s * 50}%` } : { right: "50%", width: `${-s * 50}%` }} />
    </div>
  );
}

/** 가치평가 풋볼 필드: 방법별 범위 막대 + 현재가 세로선 (투자은행 밸류에이션 요약 형식) */
export function FootballField({ rows, price }: { rows: FieldRow[]; price: number }) {
  const lo = Math.min(price, ...rows.map((r) => r.low)) * 0.95;
  const hi = Math.max(price, ...rows.map((r) => r.high)) * 1.05;
  const pos = (v: number) => `${((v - lo) / (hi - lo)) * 100}%`;
  return (
    <div className="ff" role="table" aria-label="가치평가 방법별 적정가 범위">
      {rows.map((r) => (
        <div className="ff-row" role="row" key={r.method}>
          <div className="ff-name" role="cell">{r.method}</div>
          <div className="ff-track" role="cell">
            <div className={`ff-bar ${r.mid >= price ? "up" : "down"}`} style={{ left: pos(r.low), width: `calc(${pos(r.high)} - ${pos(r.low)} + 2px)` }} />
            <div className="ff-mid" style={{ left: pos(r.mid) }} title={usd(r.mid)} />
            <div className="ff-price" style={{ left: pos(price) }} />
          </div>
          <div className="ff-val num" role="cell">{r.low === r.high ? usd(r.mid) : `${usd(r.low)} ~ ${usd(r.high)}`}</div>
        </div>
      ))}
      <div className="ff-legend">세로선 = 현재가 {usd(price)} · 막대 안 점 = 기준값 · 빨강 = 현재가보다 높음(저평가 쪽) · 파랑 = 낮음(고평가 쪽)</div>
    </div>
  );
}
