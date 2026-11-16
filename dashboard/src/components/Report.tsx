/** 결과 요약부: 판단 히어로, 모듈 등급 카드(누르면 해당 탭), 근거, 매매 계획 요약, 몬테카를로, 리스크·추세. */
import type { Horizon, Result } from "../api";
import { HORIZON_LABEL, HORIZONS, dirClass, num, pct, signed, usd } from "../format";
import { FanChart, Gauge, ScoreBar } from "./Charts";
import { Term } from "./Glossary";

export function VerdictHero({ r, horizon, onHorizon }: { r: Result; horizon: Horizon; onHorizon: (h: Horizon) => void }) {
  const v = r.verdicts[horizon];
  const tone = v.verdict === "buy" ? "up" : v.verdict === "sell" ? "down" : "hold";
  return (
    <section className={`card verdict tone-${tone}`} aria-labelledby="verdict-title">
      <div className="verdict-main">
        <div>
          <p className="eyebrow">{HORIZON_LABEL[horizon]} 판단</p>
          <h2 id="verdict-title" className="verdict-label" tabIndex={-1}>{v.strong ? "강한 " : ""}{v.label}</h2>
          <p className="verdict-meta">
            종합 점수 <b className="num">{signed(v.score * 100, 0)}</b> · <Term k="신뢰도" /> <b className="num">{pct(v.confidence, 0, false)}</b>{" "}
            · 근거 <Term k="일치도" /> <b className="num">{pct(v.agreement, 0, false)}</b>
          </p>
        </div>
        <Gauge score={v.score} label={v.label} />
      </div>
      <div className="horizon-chips" role="group" aria-label="판단 기간 바꾸기">
        {HORIZONS.map((h) => {
          const x = r.verdicts[h];
          return (
            <button key={h} aria-pressed={h === horizon} className={`chip v-${x.verdict}${h === horizon ? " on" : ""}`} onClick={() => onHorizon(h)}>
              <span>{HORIZON_LABEL[h]}</span><b>{x.label}</b><span className="num">{signed(x.score * 100, 0)}</span>
            </button>
          );
        })}
      </div>
      <div className="narrative-head">
        <h3>{r.narrative.headline}</h3>
        <p>{r.narrative.summary}</p>
      </div>
      <div className={`advice${r.advice.holding ? " holding" : ""}`}>
        <span className="advice-tag">{r.advice.holding ? `보유 중 · 평가손익 ${pct(r.advice.pnl)}` : "미보유 기준"}</span>
        <p>{r.advice.text}</p>
      </div>
      {v.notes.length > 0 && <ul className="notes">{v.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
    </section>
  );
}

/** 모듈 → 자세히 볼 탭 */
export const MODULE_TAB: Record<string, string> = {
  technical: "chart", strategies: "strategies", analogs: "simulation", sentiment: "news", consensus: "fundamentals",
  valuation: "valuation", quality: "fundamentals", personas: "personas", regime: "chart",
};

export function FactorGrid({ r, horizon, onOpen }: { r: Result; horizon: Horizon; onOpen: (tab: string) => void }) {
  const v = r.verdicts[horizon];
  return (
    <section aria-labelledby="factor-title">
      <h2 id="factor-title" className="section-title">모듈별 등급 <span className="sub">점수 -100(매도) ~ +100(매수) · 가중치는 {HORIZON_LABEL[horizon]} 기준 · 카드를 누르면 자세히</span></h2>
      <div className="factor-grid">
        {r.modules.map((m) => {
          const contrib = v.contributions[m.key];
          return (
            <button key={m.key} type="button" className={`card factor${m.available ? "" : " off"}`} onClick={() => onOpen(MODULE_TAB[m.key] ?? "summary")}
                    aria-label={`${m.label} 등급 ${m.grade}, 점수 ${m.available ? signed(m.score * 100, 0) : "없음"}. 자세히 보기`}>
              <span className="factor-head">
                <span className={`grade g-${m.grade.replace("-", "na")}`} aria-hidden="true">{m.grade}</span>
                <span className="factor-title">{m.label}</span>
                <span className={`num score ${dirClass(m.score)}`}>{m.available ? signed(m.score * 100, 0) : "-"}</span>
              </span>
              <ScoreBar score={m.score} muted={!m.available} />
              <span className="summary">{m.summary}</span>
              <span className="factor-foot">
                <span>신뢰도 {m.available ? pct(m.confidence, 0, false) : "-"}</span>
                <span>가중치 {pct(v.weights[m.key] ?? 0, 0, false)}</span>
                <span className={dirClass(contrib)}>기여 {contrib === undefined ? "-" : signed(contrib * 100, 1)}</span>
                <span className="more" aria-hidden="true">자세히 →</span>
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

export function Reasons({ r }: { r: Result }) {
  const n = r.narrative;
  const col = (title: string, items: string[], cls: string) => (
    <div className={`reason ${cls}`}>
      <h3>{title}</h3>
      {items.length ? <ul>{items.map((x) => <li key={x}>{x}</li>)}</ul> : <p className="hint">해당 없음</p>}
    </div>
  );
  return (
    <section className="card reasons" aria-label="판단 근거">
      {col("매수 근거", n.bull, "r-up")}
      {col("매도 근거", n.bear, "r-down")}
      {col("주의할 위험", n.risks, "r-warn")}
      {col("판단이 바뀌는 조건", n.triggers, "r-accent")}
      <p className="hint wide">해설: {r.meta.llm_model ?? "LLM 없음"}{r.meta.narrative_error ? ` (실패해 계량 결과로 대신함: ${r.meta.narrative_error})` : ""}. 판단 자체는 계량 엔진이 정하고 해설은 바꾸지 못합니다.</p>
    </section>
  );
}

/** 요약 탭용 매매 계획 한 줄 카드 */
export function PlanSummary({ r, horizon, onOpen }: { r: Result; horizon: Horizon; onOpen: () => void }) {
  const p = r.simulation.plans[horizon];
  const s = r.simulation.summary[horizon];
  return (
    <section className="card plan-summary" aria-label="매매 계획 요약">
      <div><span className="k">진입</span><b className="num">{usd(p.entry)}</b></div>
      <div><span className="k">손절</span><b className="num down">{usd(p.stop)}</b><small className="num">{pct(p.stop_pct)}</small></div>
      <div><span className="k">목표</span><b className="num up">{usd(p.target)}</b><small className="num">{pct(p.target_pct)}</small></div>
      <div><span className="k">목표 먼저 / 손절 먼저</span><b className="num">{pct(p.p_target_first, 0, false)} / {pct(p.p_stop_first, 0, false)}</b></div>
      <div><span className="k">{HORIZON_LABEL[horizon]} 뒤 90% 범위</span><b className="num">{pct(s.p5, 0)} ~ {pct(s.p95, 0)}</b></div>
      <div><span className="k">최대 비중</span><b className="num">{pct(p.max_weight, 0, false)}</b></div>
      <button className="btn ghost" onClick={onOpen}>시뮬레이션 자세히 →</button>
    </section>
  );
}

export function PlanCard({ r, horizon }: { r: Result; horizon: Horizon }) {
  const p = r.simulation.plans[horizon];
  const s = r.simulation.summary;
  return (
    <section className="card plan" aria-labelledby="plan-title">
      <h2 id="plan-title">매매 계획 · 몬테카를로 시뮬레이션 <span className="sub">{HORIZON_LABEL[horizon]}</span></h2>
      <div className="plan-grid">
        <dl className="kv">
          <div><dt>진입가</dt><dd className="num">{usd(p.entry)}</dd></div>
          <div><dt>손절가</dt><dd className="num down">{usd(p.stop)} <small>{pct(p.stop_pct)}</small></dd></div>
          <div><dt>목표가</dt><dd className="num up">{usd(p.target)} <small>{pct(p.target_pct)}</small></dd></div>
          <div><dt><Term k="손익비" /></dt><dd className="num">{num(p.reward_risk, 2)} : 1</dd></div>
          <div><dt>목표 먼저 도달</dt><dd className="num up">{pct(p.p_target_first, 0, false)}</dd></div>
          <div><dt>손절 먼저 도달</dt><dd className="num down">{pct(p.p_stop_first, 0, false)}</dd></div>
          <div><dt>기대 수익 (시뮬)</dt><dd className={`num ${dirClass(p.expected_return, 0.001)}`}>{pct(p.expected_return)}</dd></div>
          <div><dt>계좌 대비 최대 비중</dt><dd className="num">{pct(p.max_weight, 0, false)}{p.kelly_half !== null && <small> · 하프 켈리 {pct(p.kelly_half, 0, false)}</small>}</dd></div>
        </dl>
        <div>
          <FanChart fan={s.fan} days={s[horizon].days} price={p.entry} stop={p.stop} target={p.target} />
          <p className="hint">손절·목표 = <Term k="ATR" />({num(p.atr, 2)}) x {p.atr_mult[0]} / {p.atr_mult[1]}. 비중은 1회 손실을 계좌의 1%로 묶는 기준(최대 25%).
            경로 {r.simulation.paths.toLocaleString()}개 · 최근 3년 수익률 블록 부트스트랩 · 기대수익은 CAPM {pct(r.simulation.drift)} 로 맞춤(과거 추세를 미래로 늘리지 않음).</p>
        </div>
      </div>
      <div className="table-wrap"><table className="table compact">
        <caption className="sr-only">기간별 시뮬레이션 요약</caption>
        <thead><tr><th>기간</th><th>상승 확률</th><th>중앙값</th><th>5%</th><th>95%</th><th><Term k="CVaR">CVaR 95%</Term></th></tr></thead>
        <tbody>
          {HORIZONS.map((h) => (
            <tr key={h} className={h === horizon ? "on" : ""}>
              <th scope="row">{HORIZON_LABEL[h]}</th>
              <td className="num">{pct(s[h].p_up, 0, false)}</td>
              <td className={`num ${dirClass(s[h].median, 0.001)}`}>{pct(s[h].median)}</td>
              <td className="num down">{pct(s[h].p5)}</td>
              <td className="num up">{pct(s[h].p95)}</td>
              <td className="num">{pct(-s[h].cvar95)}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </section>
  );
}

const RISK_CLS: Record<string, string> = { "낮음": "low", "보통": "mid", "높음": "high", "매우 높음": "vhigh" };

export function RiskCard({ r }: { r: Result }) {
  const k = r.risk;
  return (
    <section className="card">
      <h2>리스크 <span className={`tag risk-${RISK_CLS[k.level] ?? "mid"}`}>{k.level}</span></h2>
      <dl className="kv">
        <div><dt>연 변동성</dt><dd className="num">{pct(k.vol_1y, 1, false)}</dd></div>
        <div><dt>베타 (SPY, 2년)</dt><dd className="num">{num(k.beta, 2)}</dd></div>
        <div><dt>1년 <Term k="MDD">최대 낙폭</Term></dt><dd className="num down">{pct(k.mdd_1y)}</dd></div>
        <div><dt>5년 최대 낙폭</dt><dd className="num down">{pct(k.mdd_5y)}</dd></div>
        <div><dt>하루 VaR 95%</dt><dd className="num">{pct(-k.var95_1d)}</dd></div>
        <div><dt><Term k="ATR">ATR(14)</Term></dt><dd className="num">{usd(k.atr)} <small>{pct(k.atr_pct, 1, false)}</small></dd></div>
      </dl>
    </section>
  );
}

export function TrendCard({ r }: { r: Result }) {
  const t = r.technical.detail;
  const lv = Object.entries(r.technical.levels).filter(([, x]) => x !== null) as [string, number][];
  return (
    <section className="card">
      <h2>추세 · 시장 국면</h2>
      <dl className="kv">
        <div><dt>1개월</dt><dd className={`num ${dirClass(t.ret_1m as number, 0.001)}`}>{pct(t.ret_1m as number)}</dd></div>
        <div><dt>3개월</dt><dd className={`num ${dirClass(t.ret_3m as number, 0.001)}`}>{pct(t.ret_3m as number)}</dd></div>
        <div><dt>6개월</dt><dd className={`num ${dirClass(t.ret_6m as number, 0.001)}`}>{pct(t.ret_6m as number)}</dd></div>
        <div><dt>1년</dt><dd className={`num ${dirClass(t.ret_12m as number, 0.001)}`}>{pct(t.ret_12m as number)}</dd></div>
        <div><dt>RSI(14)</dt><dd className="num">{num(t.rsi14 as number, 0)}</dd></div>
        <div><dt>ADX(14)</dt><dd className="num">{num(t.adx as number, 0)}</dd></div>
      </dl>
      <p className="hint">{r.technical.summary}</p>
      <p className="hint">{r.regime.summary}</p>
      {lv.length > 0 && (
        <>
          <h3>주요 가격대</h3>
          <ul className="levels">
            {lv.sort((a, b) => b[1] - a[1]).map(([k, x]) => (
              <li key={k} className={x >= r.quote.price ? "above" : "below"}><span>{k}</span><span className="num">{usd(x)}</span>
                <small className="num">{pct(x / r.quote.price - 1)}</small></li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
