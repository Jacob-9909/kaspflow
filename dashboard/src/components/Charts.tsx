/**
 * 차트 모음. 시계열(캔들·누적수익)은 TradingView lightweight-charts, 분포·범위 그림은 가벼운 인라인 SVG 와 HTML.
 * 색은 CSS 변수에서 읽는다 (테마가 바뀌면 theme prop 이 바뀌어 차트를 다시 만든다).
 */
import { useEffect, useRef } from "react";
import {
  CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart, createSeriesMarkers,
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
    const series = chart.addSeries(CandlestickSeries, { upColor: up, downColor: down, borderUpColor: up, borderDownColor: down,
      wickUpColor: up, wickDownColor: down });
    series.setData(candles.map(([t, o, h, l, c]) => ({ time: t as UTCTimestamp, open: o, high: h, low: l, close: c })));
    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false, priceLineVisible: false });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
    vol.setData(candles.map(([t, o, , , c, v]) => ({ time: t as UTCTimestamp, value: v, color: c >= o ? css("--up-weak") : css("--down-weak") })));
    const line = (pts: Point[] | undefined, color: string) => {
      if (!pts?.length) return;
      const s = chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(pts.map(([t, v]) => ({ time: t as UTCTimestamp, value: v })));
    };
    line(sma50, css("--sma50"));
    line(sma200, css("--sma200"));
    const times = new Set(candles.map((c) => c[0]));
    const mk: SeriesMarker<Time>[] = (markers ?? []).filter((m) => times.has(m.time)).sort((a, b) => a.time - b.time).map((m) =>
      m.side === "buy"
        ? { time: m.time as UTCTimestamp, position: "belowBar", shape: "arrowUp", color: up }
        : { time: m.time as UTCTimestamp, position: "aboveBar", shape: "arrowDown", color: down, text: m.ret === undefined ? "매도" : pct(m.ret, 1) });
    createSeriesMarkers(series, mk);   // lightweight-charts 5: 표시는 시리즈 메서드가 아니라 플러그인으로 붙인다
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
      const s = chart.addSeries(LineSeries, { color, lineWidth: 2, title, priceLineVisible: false, priceFormat: { type: "custom", formatter: (v: number) => pct(v - 1, 0) } });
      s.setData(pts.map(([t, v]) => ({ time: t as UTCTimestamp, value: v })));
    };
    add(b, css("--text-faint"), bLabel);
    add(a, css("--accent"), aLabel);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [a, b, aLabel, bLabel, theme]);
  return <div ref={el} className="chart" style={{ height: 240 }} role="img" aria-label={`${aLabel} 와 ${bLabel} 누적 수익 비교`} />;
}

/** 몬테카를로 팬 차트: 5~95% / 25~75% 띠 + 중앙값 + 현재가·손절·목표선.
 *  띠와 선만 SVG 로 늘이고, 가격 이름표는 HTML 로 오른쪽에 붙인다 (SVG 글자는 좁은 화면에서 같이 줄어 읽기 어렵다). */
export function FanChart({ fan, days, price, stop, target }: { fan: Fan; days: number; price: number; stop?: number; target?: number }) {
  const n = fan.days.findIndex((d) => d >= days);
  const k = n < 0 ? fan.days.length : n + 1;
  const xs = [0, ...fan.days.slice(0, k)];
  const series = (arr: number[]) => [price, ...arr.slice(0, k)];
  const p5 = series(fan.p5), p25 = series(fan.p25), p50 = series(fan.p50), p75 = series(fan.p75), p95 = series(fan.p95);
  const lo = Math.min(...p5, stop ?? Infinity) * 0.98, hi = Math.max(...p95, target ?? -Infinity) * 1.02;
  const y = (v: number) => (1 - (v - lo) / (hi - lo)) * 100; // 위에서부터 %
  const pts = (arr: number[]) => arr.map((v, i) => `${((xs[i] / xs[xs.length - 1]) * 100).toFixed(2)},${y(v).toFixed(2)}`);
  const band = (top: number[], bot: number[]) => `M${[...pts(top), ...pts(bot).reverse()].join("L")}Z`;
  // 이름표는 위에서부터 목표, 현재가, 손절. 선이 가까우면 글자가 겹치므로 현재가에서 GAP 만큼은 떨어뜨린다
  const GAP = 18, yp = y(price);
  const marks = [
    ...(target === undefined ? [] : [{ cls: "lv-target", name: "목표", v: target, at: y(target), top: Math.min(y(target), yp - GAP) }]),
    { cls: "lv-price", name: "현재가", v: price, at: yp, top: yp },
    ...(stop === undefined ? [] : [{ cls: "lv-stop", name: "손절", v: stop, at: y(stop), top: Math.max(y(stop), yp + GAP) }]),
  ];
  return (
    <figure className="fan">
      <div className="fan-plot" role="img"
           aria-label={`${days}거래일 뒤 가격 분포. 중앙값 ${usd(p50[p50.length - 1])}, 90% 범위 ${usd(p5[p5.length - 1])} ~ ${usd(p95[p95.length - 1])}`}>
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          <path d={band(p95, p5)} className="band-outer" />
          <path d={band(p75, p25)} className="band-inner" />
          <path d={`M${pts(p50).join("L")}`} className="median" vectorEffect="non-scaling-stroke" />
        </svg>
        {marks.map((m) => <span key={m.cls} className={`fan-line ${m.cls}`} style={{ top: `${m.at}%` }} />)}
        {marks.map((m) => (
          <span key={m.cls} className={`fan-tag ${m.cls}`} style={{ top: `${m.top}%` }}>
            <span>{m.name}{m.v !== price && <small className="num"> {pct(m.v / price - 1, 0)}</small>}</span>
            <b className="num">{usd(m.v)}</b>
          </span>
        ))}
      </div>
      <div className="fan-axis" aria-hidden="true"><span>오늘</span><span>{days}거래일 뒤</span></div>
      <ul className="legend">
        <li><i className="sw fan-outer" />시나리오 90%</li>
        <li><i className="sw fan-inner" />가운데 50%</li>
        <li><i className="sw fan-median" />중앙값</li>
      </ul>
    </figure>
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
      <div className="ff-legend">세로선은 현재가 {usd(price)}, 막대 안 점은 기준값입니다. 빨강은 현재가보다 높음(저평가 쪽), 파랑은 낮음(고평가 쪽).</div>
    </div>
  );
}
