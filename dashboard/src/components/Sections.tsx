/** 상세 섹션: 전략 검증, 가치평가, 재무 품질·컨센서스, 투자 대가 패널, 뉴스, 유사 국면. */
import { useMemo, useState } from "react";
import type { Horizon, Result, StrategyResult } from "../api";
import { HORIZON_LABEL, date, dirClass, num, pct, signed, usd } from "../format";
import { EquityChart, FootballField, Histogram } from "./Charts";
import { Term } from "./Glossary";
import { MetaList } from "./ui";

const FAMILY: Record<string, string> = { trend: "추세", reversion: "역추세", day: "단타" };

function signalText(s: StrategyResult) {
  if (s.family === "day") return "다음 날 시가 + 변동폭 돌파 시";
  const on = s.current.decision >= 0.5;
  const fresh = s.current.fresh ? (on ? " (신규 매수)" : " (신규 매도)") : "";
  return `${on ? "보유" : "현금"}${fresh}`;
}

export function Strategies({ r, selected, onSelect, theme }: { r: Result; selected: string; onSelect: (k: string) => void; theme: string }) {
  const v = r.validation;
  const rows = useMemo(() => [...v.strategies].sort((a, b) => b.weight - a.weight || (b.wf?.metrics.sharpe ?? -9) - (a.wf?.metrics.sharpe ?? -9)), [v]);
  const sel = v.strategies.find((s) => s.key === selected) ?? rows[0];
  const bh = v.buy_hold.oos;
  return (
    <section className="card" id="sec-strategies" aria-labelledby="strat-title">
      <h2 id="strat-title">전략 검증 <span className="sub"><MetaList items={[`${v.start} ~ ${v.end}`, `${v.bars.toLocaleString()}봉`, `변형 ${v.n_trials}개`, `워크포워드 ${v.folds}구간`]} /></span></h2>
      <p className="hint">숫자는 모두 표본 밖(OOS) 성과입니다. 직전 3년에서 고른 파라미터를 다음 1년에 그대로 써서 이어 붙였습니다. 체결은 다음 날 시가, 편도 비용 0.05%, 현금은 단기 국채 금리.
        순열 p값 = 같은 보유 패턴을 무작위 시점에 둘 때 이만큼 나올 확률. DSR = 변형 {v.n_trials}개를 시험한 것을 감안한 샤프 신뢰도(기준 샤프 {num(v.sr0_annual, 2)}).</p>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>전략</th><th>등급</th><th><Term k="OOS" /> <Term k="샤프" /></th><th>OOS 연수익</th><th><Term k="MDD">최대 낙폭</Term></th><th>거래</th><th>승률</th><th><Term k="p값" /></th><th><Term k="DSR" /></th><th>지금 신호</th></tr>
          </thead>
          <tbody>
            {rows.map((s) => {
              const m = s.wf?.metrics;
              return (
                <tr key={s.key} className={`${s.key === sel?.key ? "on" : ""} clickable`} onClick={() => onSelect(s.key)} tabIndex={0}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(s.key); } }}
                    aria-selected={s.key === sel?.key}>
                  <th scope="row"><span className="strat-name">{s.label}</span><span className="tag">{FAMILY[s.family]}</span><div className="origin">{s.origin}</div></th>
                  <td><span className={`grade g-${s.grade}`}>{s.grade}</span></td>
                  <td className={`num ${dirClass(m?.sharpe, 0.1)}`}>{num(m?.sharpe, 2)}</td>
                  <td className={`num ${dirClass(m?.cagr, 0.001)}`}>{pct(m?.cagr)}</td>
                  <td className="num down">{pct(m?.max_drawdown, 0)}</td>
                  <td className="num">{m?.trades ?? "-"}</td>
                  <td className="num">{pct(m?.win_rate, 0, false)}</td>
                  <td className="num">{num(s.perm_p, 3)}</td>
                  <td className="num">{num(s.dsr, 2)}</td>
                  <td className={s.current.vote > 0 ? "up" : s.current.vote < 0 ? "down" : ""}>{signalText(s)}</td>
                </tr>
              );
            })}
            {bh && (
              <tr className="baseline">
                <th scope="row">단순 보유 (기준선)</th><td>-</td><td className="num">{num(bh.sharpe, 2)}</td><td className="num">{pct(bh.cagr)}</td>
                <td className="num down">{pct(bh.max_drawdown, 0)}</td><td colSpan={5} className="hint">같은 OOS 구간을 그냥 들고 있었을 때</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {sel && (
        <div className="strat-detail">
          <div>
            <h3>{sel.label} <span className="sub">{sel.rule}</span></h3>
            {sel.wf ? <EquityChart a={sel.wf.equity} b={sel.wf.bh_equity} aLabel={sel.label} bLabel="단순 보유" theme={theme} /> : <p className="hint">워크포워드에 쓸 기록이 부족합니다.</p>}
          </div>
          <dl className="kv">
            <div><dt>지금 쓰는 파라미터</dt><dd>{Object.entries(sel.current.params).map(([k, x]) => `${k}=${x}`).join(", ")}</dd></div>
            {Object.entries(sel.current.levels).map(([k, x]) => <div key={k}><dt>{k}</dt><dd className="num">{x === null ? "-" : Math.abs(x) < 5 && !k.includes("SMA") ? num(x, 3) : num(x, 2)}</dd></div>)}
            <div><dt>워크포워드 효율 (OOS/IS 샤프)</dt><dd className="num">{num(sel.wf?.efficiency, 2)}</dd></div>
            <div><dt>전 구간 최고 변형 (과최적화 주의)</dt><dd className="num">샤프 {num(sel.best_full.metrics.sharpe, 2)} · {Object.entries(sel.best_full.params).map(([k, x]) => `${k}=${x}`).join(", ")}</dd></div>
            {sel.mc && <div><dt>거래 재표본 1,000회</dt><dd className="num">수익 5%/50%/95% {pct(sel.mc.ret_p5, 0)} / {pct(sel.mc.ret_p50, 0)} / {pct(sel.mc.ret_p95, 0)} · 낙폭 5% {pct(sel.mc.mdd_p5, 0)}</dd></div>}
          </dl>
        </div>
      )}
      <p className="hint">등급: A(p≤0.05·샤프≥0.5·거래≥15·DSR≥0.8) B(p≤0.10·샤프≥0.3·거래≥8·DSR≥0.5) C(p≤0.25·샤프&gt;0·거래≥5) F(판단에 안 씀). 가격 차트의 화살표는 선택한 전략의 최근 2년 매매입니다.</p>
    </section>
  );
}

export function Valuation({ r }: { r: Result }) {
  const v = r.fundamentals.valuation;
  if (!v.available && !(v.football_field?.length)) return null;
  const d = v.dcf;
  const mult = v.multiples ?? {};
  const MULT: [string, string, (x: number) => string][] = [["pe", "PER", (x) => `${num(x, 1)}배`], ["pe_fwd", "선행 PER", (x) => `${num(x, 1)}배`],
    ["peg", "PEG", (x) => num(x, 2)], ["pb", "PBR", (x) => `${num(x, 1)}배`], ["ps", "PSR", (x) => `${num(x, 1)}배`],
    ["ev_ebitda", "EV/EBITDA", (x) => `${num(x, 1)}배`], ["fcf_yield", "FCF 수익률", (x) => pct(x)], ["earnings_yield", "이익수익률(EBIT/EV)", (x) => pct(x)],
    ["dividend_yield", "배당수익률", (x) => pct(x, 2, false)]];
  return (
    <section className="card" id="sec-valuation" aria-labelledby="val-title">
      <h2 id="val-title">가치평가 <span className="sub">{v.summary}</span></h2>
      <FootballField rows={v.football_field} price={r.quote.price} />
      <div className="two-col">
        {d && (
          <div>
            <h3>DCF (FCFF, 2단계 + 영구성장)</h3>
            <dl className="kv">
              <div><dt>주당 내재가치</dt><dd className={`num ${d.per_share >= r.quote.price ? "up" : "down"}`}>{usd(d.per_share)}</dd></div>
              <div><dt><Term k="WACC" /></dt><dd className="num">{pct(d.wacc, 1, false)} <small>자기자본비용 {pct(d.cost_of_equity, 1, false)} · 조정 베타 {num(d.beta, 2)}</small></dd></div>
              <div><dt>1~5년 성장률</dt><dd className="num">{pct(d.growth)} <small>6~10년 영구성장률 {pct(d.terminal_growth, 1, false)} 로 수렴</small></dd></div>
              <div><dt>현재가가 가정하는 성장률 (<Term k="역DCF" />)</dt><dd className={`num ${d.implied_growth > d.growth ? "down" : "up"}`}>{pct(d.implied_growth)}</dd></div>
              <div><dt>터미널 가치 비중</dt><dd className="num">{pct(d.tv_share, 0, false)}</dd></div>
            </dl>
            <p className="hint">성장률 근거: {d.growth_sources.join(", ") || "-"} (중앙값, -5%~30% 제한). 주식보상(SBC)은 비용으로 뺐습니다.</p>
            <table className="table compact sens">
              <caption>민감도: WACC × 영구성장률 (주당 가치)</caption>
              <thead><tr><th>WACC \ g</th>{d.sensitivity.g_term.map((g) => <th key={g}>{pct(g, 1, false)}</th>)}</tr></thead>
              <tbody>
                {d.sensitivity.values.map((row, i) => (
                  <tr key={i}><th scope="row">{pct(d.sensitivity.wacc[i], 1, false)}</th>
                    {row.map((x, j) => <td key={j} className={`num ${x >= r.quote.price ? "up" : "down"}${i === 1 && j === 1 ? " base" : ""}`}>{usd(x)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div>
          <h3>배수</h3>
          <dl className="kv">
            {MULT.filter(([k]) => typeof mult[k] === "number").map(([k, label, f]) => <div key={k}><dt>{label}</dt><dd className="num">{f(mult[k] as number)}</dd></div>)}
          </dl>
          {v.notes?.map((n) => <p key={n} className="hint warn">{n}</p>)}
          <p className="hint">적정가 = DCF 50% · PEG 30% · 그레이엄 공식 20% 가중. EPV(무성장)와 그레이엄 넘버는 보수적 하한으로만 표시합니다.</p>
        </div>
      </div>
    </section>
  );
}

export function Fundamentals({ r }: { r: Result }) {
  const { quality: q, consensus: c, facts } = r.fundamentals;
  if (!q.available && !c.available) return null;
  const detail = (c.detail ?? {}) as Record<string, unknown>;
  return (
    <section className="card" id="sec-fundamentals" aria-labelledby="fund-title">
      <h2 id="fund-title">재무 품질과 애널리스트</h2>
      <div className="three-col">
        <div>
          <h3>품질 점수</h3>
          <ul className="bars">
            {Object.entries(q.groups).map(([k, x]) => (
              <li key={k}><span>{k}</span><span className={`num ${dirClass(x)}`}>{signed(x * 100, 0)}</span></li>
            ))}
          </ul>
          {q.altman_z !== null && <p className="hint"><Term k="알트만Z">알트만 Z</Term> {num(q.altman_z, 2)} ({q.altman_z > 2.99 ? "안전" : q.altman_z > 1.81 ? "회색 지대" : "부실 위험"})</p>}
          {q.piotroski && (
            <>
              <h3><Term k="피오트로스키">피오트로스키 F-점수</Term> {q.piotroski.score}/{q.piotroski.max}</h3>
              <ul className="checks">
                {q.piotroski.checks.map((x) => <li key={x.name} className={x.pass === null ? "na" : x.pass ? "pass" : "fail"}>{x.name}</li>)}
              </ul>
            </>
          )}
        </div>
        <div>
          <h3>핵심 지표</h3>
          <dl className="kv dense">{Object.entries(facts).map(([k, x]) => <div key={k}><dt>{k}</dt><dd className="num">{x}</dd></div>)}</dl>
        </div>
        <div>
          <h3>애널리스트 컨센서스</h3>
          <p>{c.summary}</p>
          {"rating_changes_90d" in detail && (
            <p className="hint">최근 90일 등급 상향 {(detail.rating_changes_90d as { up: number }).up}건 · 하향 {(detail.rating_changes_90d as { down: number }).down}건</p>
          )}
          {c.recent && c.recent.length > 0 && (
            <ul className="actions">
              {c.recent.slice(0, 6).map((a, i) => (
                <li key={i}><span className="date">{a.date}</span> {a.firm} <span className={a.action === "up" ? "up" : a.action === "down" ? "down" : ""}>{a.from && a.from !== a.to ? `${a.from} → ` : ""}{a.to}</span>{a.target ? ` · 목표 ${usd(a.target)}` : ""}</li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

const SIGNAL: Record<string, [string, string]> = { bullish: ["긍정", "up"], neutral: ["중립", "flat"], bearish: ["부정", "down"] };

export function Personas({ r }: { r: Result }) {
  const members = Object.entries(r.personas.members);
  if (!members.length) return null;
  return (
    <section id="sec-personas" aria-labelledby="persona-title">
      <h2 id="persona-title" className="section-title">투자 대가 패널 <span className="sub">{r.personas.summary}</span></h2>
      {r.personas.error && <p className="hint warn">{r.personas.error}</p>}
      <div className="persona-grid">
        {members.map(([k, p]) => {
          const sig = p.view ? SIGNAL[p.view.signal] : null;
          const passed = p.checklist.filter((c) => c.pass).length, avail = p.checklist.filter((c) => c.pass !== null).length;
          return (
            <article key={k} className="card persona">
              <header>
                <span className="avatar" aria-hidden="true">{p.name.slice(0, 1)}</span>
                <div><h3>{p.name}</h3><p className="hint">{p.philosophy}</p></div>
                <span className={`num score ${dirClass(p.score)}`}>{p.score === null ? "-" : signed(p.score * 100, 0)}</span>
              </header>
              {p.view ? (
                <>
                  <p className="thesis"><span className={`tag ${sig?.[1]}`}>{sig?.[0]} {p.view.confidence}</span> {p.view.thesis}</p>
                  <div className="pn">
                    <ul className="plus">{p.view.positives.map((x) => <li key={x}>{x}</li>)}</ul>
                    <ul className="minus">{p.view.negatives.map((x) => <li key={x}>{x}</li>)}</ul>
                  </div>
                </>
              ) : <p className="hint">AI 의견 없음 (체크리스트 점수만 사용{p.error ? `: ${p.error}` : ""})</p>}
              <details>
                <summary>체크리스트 {passed}/{avail} 통과</summary>
                <ul className="checks">{p.checklist.map((c) => <li key={c.name} className={c.pass === null ? "na" : c.pass ? "pass" : "fail"}>{c.name}{c.value ? ` (${c.value})` : ""}</li>)}</ul>
              </details>
            </article>
          );
        })}
      </div>
      <p className="hint">실제 인물의 견해가 아니라, 각 투자 철학을 규칙(체크리스트)과 AI 해석으로 흉내 낸 것입니다. 점수 = 체크리스트 50% + AI 의견 50%.</p>
    </section>
  );
}

const EVENT_KO: Record<string, string> = { earnings: "실적", guidance: "가이던스", analyst: "애널리스트", product: "제품·사업", legal: "소송·규제",
  mna: "M&A", management: "경영진", capital: "자본·배당", macro: "거시·업황", price_move: "주가 등락", other: "기타" };

export function News({ r }: { r: Result }) {
  const { aggregate: a, items, error } = r.news;
  const [all, setAll] = useState(false);
  const shown = (all ? items : items.filter((x) => (x.relevance ?? 1) >= 0.5)).slice(0, all ? 40 : 15);
  return (
    <section className="card" id="sec-news" aria-labelledby="news-title">
      <h2 id="news-title">뉴스 감성 <span className="sub">{a.summary}</span></h2>
      {error && <p className="hint warn">{error}</p>}
      {a.available && (
        <p className="hint">최근 7일 {signed(a.recent_7d ?? null)} · 그 이전 {signed(a.older ?? null)} · 사건: {Object.entries(a.events ?? {}).map(([k, x]) => `${k} ${x}`).join(", ") || "-"}</p>
      )}
      <ul className="news">
        {shown.map((x) => (
          <li key={x.id}>
            {x.sentiment !== undefined && <span className={`senti ${dirClass(x.sentiment, 0.15)}`} aria-label={`감성 ${signed(x.sentiment, 1)}`}>{signed(x.sentiment, 1)}</span>}
            <div>
              <a href={x.url} target="_blank" rel="noreferrer noopener">{x.title_ko || x.title}</a>
              <div className="meta"><MetaList items={[x.source, date(x.published), x.event ? (EVENT_KO[x.event] ?? x.event) : null]} />{x.title_ko && <div>{x.title}</div>}</div>
            </div>
          </li>
        ))}
      </ul>
      {items.length > 15 && <button className="btn outline" onClick={() => setAll(!all)}>{all ? "관련 기사만" : `전체 ${items.length}건 보기`}</button>}
    </section>
  );
}

export function Analogs({ r, horizon }: { r: Result; horizon: Horizon }) {
  const a = r.analogs[horizon];
  return (
    <section className="card" aria-labelledby="analog-title">
      <h2 id="analog-title">과거 유사 국면 <span className="sub">{HORIZON_LABEL[horizon]}</span></h2>
      <p>{a.summary}</p>
      {a.available && a.hist && (
        <>
          <Histogram counts={a.hist.counts} edges={a.hist.edges} />
          <p className="hint">표본 {a.n}일 (겹침을 감안한 유효 표본 {a.n_eff}) · 평균 {pct(a.mean)} vs 전체 {pct(a.base_mean)} · t = {num(a.t_stat, 2)}. |t| 가 2 미만이면 우연과 구별하기 어렵습니다.</p>
        </>
      )}
    </section>
  );
}
