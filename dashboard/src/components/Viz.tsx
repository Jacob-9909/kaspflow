/**
 * 모듈 결과를 글 대신 그림으로 읽게 하는 작은 차트들. 라이브러리 없이 HTML 로 그린다
 * (SVG 는 화면이 좁아지면 글자까지 같이 줄어 모바일에서 읽기 어렵다).
 *
 * 색은 시장 뜻에만: 매수 쪽 --up(빨강), 매도 쪽 --down(파랑), 나머지는 무채색.
 * 빨강과 관망 호박색은 색맹에게 구분이 안 되므로 한 차트 안에 나란히 두지 않는다.
 * 값은 글자로도 함께 보여 주고(색만으로 뜻을 전하지 않음), 막대·점에 마우스를 올리면 title 로 자세한 값을 띄운다.
 */
import type { ReactNode } from "react";
import type { HorizonSim, NewsItem, Plan } from "../api";
import { date, dirClass, pct, signed, usd } from "../format";

type Tone = "up" | "down" | "flat";
const toneOf = (x: number, dead = 0): Tone => (x > dead ? "up" : x < -dead ? "down" : "flat");
const clamp = (x: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, x));

/* ---------- 판단 구성: 모듈별 반영을 차례로 더해 종합 점수에 이르는 폭포 차트 ---------- */

export interface Step { key: string; label: string; value: number }

/** 매수 쪽(플러스)을 먼저 큰 순서로, 이어서 매도 쪽(마이너스)을 큰 순서로 쌓는다. 마지막 줄이 종합 점수.
 *  marks 는 판단 기준선(점선): 종합 점수 막대가 이 선을 넘으면 매수·매도 판단이 선다. */
export function Waterfall({ steps, total, fmt, marks = [] }: {
  steps: Step[]; total: number; fmt: (x: number) => string; marks?: { at: number; label: string }[];
}) {
  const ordered = [...steps.filter((s) => s.value > 0).sort((a, b) => b.value - a.value),
    ...steps.filter((s) => s.value <= 0).sort((a, b) => a.value - b.value)];
  let run = 0;
  const rows = ordered.map((s) => { const from = run; run += s.value; return { ...s, from, to: run }; });
  const ends = [0, total, ...marks.map((m) => m.at), ...rows.flatMap((r) => [r.from, r.to])];
  const lo = Math.min(...ends), hi = Math.max(...ends), pad = (hi - lo) * 0.06 || 0.01;
  const x = (v: number) => ((v - (lo - pad)) / (hi - lo + 2 * pad)) * 100;
  const bar = (from: number, to: number) => ({ left: `${x(Math.min(from, to))}%`, width: `max(3px, ${Math.abs(x(to) - x(from))}%)` });
  const guides = (
    <>
      <span className="wf-zero" style={{ left: `${x(0)}%` }} />
      {marks.map((m) => <span key={m.at} className="wf-mark" style={{ left: `${x(m.at)}%` }} />)}
    </>
  );
  const row = (key: string, label: string, from: number, to: number, cls = "") => (
    <div className={`wf-row ${cls}`} role="row" key={key}>
      <span className="wf-label" role="rowheader">{label}</span>
      <span className="wf-track" role="cell">
        {guides}
        <span className={`wf-bar ${toneOf(to - from)}`} style={bar(from, to)}
              title={cls ? `${label} ${fmt(to)}` : `${label} ${fmt(to - from)} (누적 ${fmt(to)})`} />
      </span>
      <span className={`wf-val num ${dirClass(to - from, 0.0005)}`} role="cell">{fmt(to - from)}</span>
    </div>
  );
  return (
    <div className="wf" role="table" aria-label="모듈별 반영이 종합 점수를 만든 과정">
      {rows.map((r) => row(r.key, r.label, r.from, r.to))}
      {row("total", "종합 점수", 0, total, "total")}
      {marks.length > 0 && (
        <div className="wf-row wf-axis" aria-hidden="true">
          <span />
          <span className="wf-track">{marks.map((m) => <em key={m.at} style={{ left: `${x(m.at)}%` }}>{m.label}</em>)}</span>
          <span />
        </div>
      )}
    </div>
  );
}

/* ---------- 한 덩어리를 나눈 막대 (확률, 투표, 상향·하향 건수) ---------- */

export interface Part { key: string; label: string; value: number; tone: Tone; note?: string }

/** 칸 안에는 몫(%)만, 이름은 아래 범례에. 칸이 좁아 몫을 못 쓰면 범례에 몫을 붙인다. */
export function SegBar({ parts, label, unit }: { parts: Part[]; label: string; unit?: (p: Part) => string }) {
  const sum = parts.reduce((a, p) => a + Math.max(0, p.value), 0);
  if (sum <= 0) return null;
  const share = (p: Part) => Math.max(0, p.value) / sum;
  const fits = (p: Part) => share(p) >= 0.15; // 좁은 화면(막대 300px)에서도 "38%" 가 칸 안에 들어가는 폭
  const text = (p: Part) => (unit ? unit(p) : pct(share(p), 0, false));
  return (
    <figure className="segbar">
      <figcaption>{label}</figcaption>
      <div className="segbar-bar" role="img" aria-label={`${label}: ${parts.map((p) => `${p.label} ${text(p)}`).join(", ")}`}>
        {parts.filter((p) => p.value > 0).map((p) => (
          <span key={p.key} className={`seg ${p.tone}`} style={{ flexGrow: share(p) }} title={`${p.label} ${text(p)}${p.note ? `: ${p.note}` : ""}`}>
            {fits(p) ? text(p) : ""}
          </span>
        ))}
      </div>
      <ul className="legend">
        {parts.map((p) => (
          <li key={p.key} title={p.note}><i className={`sw ${p.tone}`} />{p.label}{!fits(p) && p.value > 0 && <span className="num"> {text(p)}</span>}</li>
        ))}
      </ul>
    </figure>
  );
}

/* ---------- 매매 계획: 손절·진입·목표 가격 사다리 + 먼저 닿을 확률 ---------- */

export function PlanVisual({ p, days }: { p: Plan; days: number }) {
  const lo = p.stop, hi = p.target, pad = (hi - lo) * 0.03;
  const x = (v: number) => ((v - (lo - pad)) / (hi - lo + 2 * pad)) * 100;
  const xe = x(p.entry);
  return (
    <div className="plan-vis">
      <div className="ladder" role="img" aria-label={`손절가 ${usd(p.stop)} (${pct(p.stop_pct)}), 진입가 ${usd(p.entry)}, 목표가 ${usd(p.target)} (${pct(p.target_pct)})`}>
        <div className="ladder-top"><span style={{ left: `${clamp(xe, 12, 88)}%` }}>진입 <b className="num">{usd(p.entry)}</b></span></div>
        <div className="ladder-track">
          <span className="ladder-seg down" style={{ left: `${x(lo)}%`, width: `${xe - x(lo)}%` }} />
          <span className="ladder-seg up" style={{ left: `${xe}%`, width: `${x(hi) - xe}%` }} />
          <span className="ladder-pin down" style={{ left: `${x(lo)}%` }} />
          <span className="ladder-pin entry" style={{ left: `${xe}%` }} />
          <span className="ladder-pin up" style={{ left: `${x(hi)}%` }} />
        </div>
        <div className="ladder-ends">
          <span>손절 <b className="num">{usd(p.stop)}</b> <small className="num">{pct(p.stop_pct)}</small></span>
          <span>목표 <b className="num">{usd(p.target)}</b> <small className="num">{pct(p.target_pct)}</small></span>
        </div>
      </div>
      <SegBar label={`${days}거래일 안에 먼저 닿을 확률 (시뮬레이션)`} parts={[
        { key: "stop", label: "손절 먼저", value: p.p_stop_first, tone: "down" },
        { key: "none", label: "둘 다 안 닿음", value: p.p_neither, tone: "flat" },
        { key: "target", label: "목표 먼저", value: p.p_target_first, tone: "up" },
      ]} />
    </div>
  );
}

/* ---------- 범위 막대: 5~95% 수염, 25~75% 상자, 중앙값 ---------- */

export interface Range { key: string; label: ReactNode; sim: HorizonSim; on?: boolean }

export function RangeRows({ rows, label }: { rows: Range[]; label: string }) {
  const lo = Math.min(0, ...rows.map((r) => r.sim.p5)), hi = Math.max(0, ...rows.map((r) => r.sim.p95));
  const pad = (hi - lo) * 0.04;
  const x = (v: number) => `${((v - (lo - pad)) / (hi - lo + 2 * pad)) * 100}%`;
  const w = (a: number, b: number) => `calc(${x(b)} - ${x(a)})`;
  return (
    <div className="rr" role="table" aria-label={label}>
      {rows.map(({ key, label: name, sim: s, on }) => (
        <div key={key} className={`rr-row${on ? " on" : ""}`} role="row"
             title={`5% ${pct(s.p5)} · 25% ${pct(s.p25)} · 중앙값 ${pct(s.median)} · 75% ${pct(s.p75)} · 95% ${pct(s.p95)}`}>
          <span className="rr-label" role="rowheader">{name}</span>
          <span className="rr-track" role="cell" aria-label={`5% ${pct(s.p5)}부터 95% ${pct(s.p95)}, 중앙값 ${pct(s.median)}`}>
            <span className="rr-zero" style={{ left: x(0) }} />
            <span className="rr-whisker" style={{ left: x(s.p5), width: w(s.p5, s.p95) }} />
            <span className="rr-box" style={{ left: x(s.p25), width: w(s.p25, s.p75) }} />
            <span className={`rr-mid ${toneOf(s.median, 0.001)}`} style={{ left: x(s.median) }} />
          </span>
          <span className="rr-aside" role="cell">
            <span className={`num ${dirClass(s.median, 0.001)}`}>{pct(s.median)}</span>
            <small>오를 확률 <span className="num">{pct(s.p_up, 0, false)}</span></small>
          </span>
        </div>
      ))}
      <div className="rr-row rr-axis" aria-hidden="true">
        <span />
        <span className="rr-track">
          <span style={{ left: x(lo) }}>{pct(lo, 0)}</span>
          {lo < 0 && hi > 0 && <span style={{ left: x(0) }}>0%</span>}
          <span style={{ left: x(hi) }}>{pct(hi, 0)}</span>
        </span>
        <span />
      </div>
    </div>
  );
}

/* ---------- 가운데 0 기준 막대 목록 (모듈 안의 세부 점수, 수익률 비교, 건수) ---------- */

export interface Bar { key: string; label: ReactNode; value: number | null; note?: string }

/** 값이 모두 0 이상이면 왼쪽 끝이 0, 아니면 가운데가 0. neutral 이면 부호와 상관없이 무채색(건수, 비교용). */
export function DivBars({ rows, fmt, label, max, neutral = false }: {
  rows: Bar[]; fmt: (x: number) => string; label: string; max?: number; neutral?: boolean;
}) {
  const vals = rows.map((r) => r.value).filter((v): v is number => v !== null && Number.isFinite(v));
  const m = max ?? Math.max(...vals.map(Math.abs), 1e-9);
  const oneSided = vals.every((v) => v >= 0);
  const pos = (v: number) => (oneSided ? clamp(v / m, 0, 1) * 100 : 50 + clamp(v / m, -1, 1) * 50);
  const z = oneSided ? 0 : 50;
  return (
    <div className={`db${oneSided ? " one" : ""}`} role="table" aria-label={label}>
      {rows.map((r) => {
        const v = r.value;
        const ok = v !== null && Number.isFinite(v);
        const tone = neutral || !ok ? "flat" : toneOf(v as number);
        return (
          <div className="db-row" role="row" key={r.key} title={r.note}>
            <span className="db-label" role="rowheader">{r.label}</span>
            <span className="db-track" role="cell">
              {!oneSided && <span className="db-zero" />}
              {ok && <span className={`db-fill ${tone}${(v as number) < 0 ? " neg" : ""}`}
                           style={{ left: `${Math.min(z, pos(v as number))}%`, width: `${Math.abs(pos(v as number) - z)}%` }} />}
            </span>
            <span className={`db-val num ${neutral ? "" : dirClass(v, 0)}`} role="cell">{ok ? fmt(v as number) : "-"}</span>
          </div>
        );
      })}
    </div>
  );
}

/* ---------- 구간 눈금자: 값이 어느 구간(과매도, 부실 위험 등)에 있는지 ---------- */

export interface Zone { to: number; label: string }

/** zones 는 왼쪽부터 각 구간의 오른쪽 끝(to). 마지막 구간의 to 는 max. 값이 범위를 넘으면 끝에 붙이고 숫자는 그대로 쓴다. */
export function ZoneScale({ value, min, max, zones, fmt, label }: {
  value: number; min: number; max: number; zones: Zone[]; fmt: (x: number) => string; label: string;
}) {
  const x = (v: number) => ((clamp(v, min, max) - min) / (max - min)) * 100;
  let start = min;
  const segs = zones.map((zn) => { const s = { ...zn, from: start }; start = zn.to; return s; });
  const cur = segs.find((s) => value <= s.to) ?? segs[segs.length - 1];
  return (
    <figure className="zs">
      <figcaption>{label} <b className="num">{fmt(value)}</b> <span className="zs-now">{cur.label}</span></figcaption>
      {/* 경계 숫자는 막대 위, 구간 이름은 막대 아래 (핀이 글자를 가리지 않게) */}
      <div className="zs-ticks" aria-hidden="true">
        {segs.slice(0, -1).map((s) => <span key={s.label} style={{ left: `${x(s.to)}%` }}>{fmt(s.to)}</span>)}
      </div>
      <div className="zs-track" role="img" aria-label={`${label} ${fmt(value)}, ${cur.label} 구간 (${zones.map((z) => z.label).join(", ")})`}>
        {segs.map((s) => <span key={s.label} className={`zs-seg${s === cur ? " on" : ""}`} style={{ flexGrow: s.to - s.from }} />)}
        <span className="zs-pin" style={{ left: `${x(value)}%` }} />
      </div>
      <div className="zs-labels" aria-hidden="true">
        {segs.map((s) => <span key={s.label} className={s === cur ? "on" : ""} style={{ flexGrow: s.to - s.from }}>{s.label}</span>)}
      </div>
    </figure>
  );
}

/* ---------- 체크리스트 칸 (피오트로스키 F-점수) ---------- */

export function Pips({ checks }: { checks: { name: string; pass: boolean | null }[] }) {
  return (
    <span className="pips" role="img" aria-label={`${checks.filter((c) => c.pass).length}개 통과, ${checks.filter((c) => c.pass === false).length}개 미달`}>
      {checks.map((c) => <i key={c.name} className={c.pass === null ? "na" : c.pass ? "pass" : "fail"} title={`${c.name}: ${c.pass === null ? "데이터 없음" : c.pass ? "통과" : "미달"}`} />)}
    </span>
  );
}

/* ---------- 워크포워드 구간별 샤프: 표본 안(IS) vs 표본 밖(OOS) ---------- */

export function FoldChart({ folds }: { folds: { oos_start: string; is_sharpe: number; oos_sharpe: number }[] }) {
  const vals = folds.flatMap((f) => [f.is_sharpe, f.oos_sharpe]);
  const span = Math.max(...vals) - Math.min(...vals);
  const step = span > 6 ? 2 : span > 3 ? 1 : 0.5;
  const lo = Math.min(0, Math.floor(Math.min(...vals) / step) * step), hi = Math.max(step, Math.ceil(Math.max(...vals) / step) * step);
  const y = (v: number) => ((hi - v) / (hi - lo)) * 100; // 위에서부터 %
  const ticks: number[] = [];
  for (let t = lo; t <= hi + 1e-9; t += step) ticks.push(+t.toFixed(2));
  const col = (v: number) => (v >= 0 ? { top: `${y(v)}%`, height: `${y(0) - y(v)}%` } : { top: `${y(0)}%`, height: `${y(v) - y(0)}%` });
  return (
    <figure className="fold">
      <figcaption>워크포워드 구간별 샤프</figcaption>
      <div className="fold-plot" role="table" aria-label="워크포워드 구간별 표본 안과 표본 밖 샤프">
        {ticks.map((t) => <span key={t} className={`fold-grid${t === 0 ? " base" : ""}`} style={{ top: `${y(t)}%` }}><em>{t}</em></span>)}
        <div className="fold-cols">
          {folds.map((f) => (
            <div key={f.oos_start} className="fold-col" role="row" tabIndex={0}
                 title={`${f.oos_start.slice(0, 4)}년: 표본 안 ${f.is_sharpe.toFixed(2)}, 표본 밖 ${f.oos_sharpe.toFixed(2)}`}>
              <span className={`fold-bar is${f.is_sharpe < 0 ? " neg" : ""}`} style={col(f.is_sharpe)} role="cell" aria-label={`표본 안 ${f.is_sharpe.toFixed(2)}`} />
              <span className={`fold-bar oos${f.oos_sharpe < 0 ? " neg" : ""}`} style={col(f.oos_sharpe)} role="cell" aria-label={`표본 밖 ${f.oos_sharpe.toFixed(2)}`} />
              <em role="rowheader">{f.oos_start.slice(0, 4)}</em>
            </div>
          ))}
        </div>
      </div>
      <ul className="legend">
        <li><i className="sw is" />표본 안(IS): 파라미터를 고른 3년</li>
        <li><i className="sw oos" />표본 밖(OOS): 그다음 1년 실제 성적</li>
      </ul>
    </figure>
  );
}

/* ---------- 뉴스 감성 흐름: 기사 하나가 점 하나, 최근 7일과 그 이전 평균선 ---------- */

export function SentimentDots({ items, recent, older }: { items: NewsItem[]; recent?: number | null; older?: number | null }) {
  const pts = items.filter((n) => typeof n.sentiment === "number" && n.published)
    .map((n) => ({ n, t: Date.parse(n.published), s: n.sentiment as number })).filter((p) => Number.isFinite(p.t));
  if (pts.length < 3) return null;
  const t0 = Math.min(...pts.map((p) => p.t)), t1 = Math.max(...pts.map((p) => p.t), t0 + 864e5);
  const x = (t: number) => 2 + ((t - t0) / (t1 - t0)) * 96;
  const y = (s: number) => 50 - clamp(s, -1, 1) * 44; // 위가 긍정
  const cut = t1 - 7 * 864e5;
  const avg = (v: number | null | undefined, a: number, b: number, name: string) =>
    typeof v === "number" && b > a ? (
      <span className="sd-avg" style={{ left: `${x(a)}%`, width: `${x(b) - x(a)}%`, top: `${y(v)}%` }} title={`${name} 평균 ${signed(v, 2)}`}>
        <em>{name} {signed(v, 2)}</em>
      </span>) : null;
  return (
    <figure className="sd">
      <figcaption>기사별 감성 <small>점 하나가 기사 하나, 위가 긍정·아래가 부정. 선은 기간 평균</small></figcaption>
      <div className="sd-plot" role="img" aria-label={`기사 ${pts.length}건의 감성. 최근 7일 평균 ${signed(recent ?? null, 2)}, 그 이전 ${signed(older ?? null, 2)}`}>
        <span className="sd-grid" style={{ top: `${y(1)}%` }}><em>긍정</em></span>
        <span className="sd-grid base" style={{ top: `${y(0)}%` }}><em>중립</em></span>
        <span className="sd-grid" style={{ top: `${y(-1)}%` }}><em>부정</em></span>
        <div className="sd-area">
          {avg(older, t0, Math.max(t0, cut), "이전")}
          {avg(recent, Math.max(t0, cut), t1, "최근 7일")}
          {pts.map(({ n, t, s }) => (
            <span key={n.id} className={`sd-dot ${toneOf(s, 0.15)}${(n.relevance ?? 1) < 0.5 ? " faint" : ""}`}
                  style={{ left: `${x(t)}%`, top: `${y(s)}%` }} title={`${date(n.published)} ${signed(s, 1)} ${n.title_ko || n.title}`} />
          ))}
        </div>
      </div>
      <div className="sd-axis" aria-hidden="true"><span>{date(new Date(t0).toISOString())}</span><span>{date(new Date(t1).toISOString())}</span></div>
    </figure>
  );
}
