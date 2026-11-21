/** 결과 요약부: 판단 히어로(기간별 눈금), 지금 상황(회사와 타이밍), 매매 계획과 시뮬레이션, 근거, 점수 계산 과정, 리스크·추세. */
import type { Horizon, ModuleCard, Result } from "../api";
import { HORIZON_LABEL, HORIZON_SHORT, HORIZONS, agreementWord, dirClass, num, pct, signed, usd } from "../format";
import { FanChart, ScoreBar } from "./Charts";
import { Term } from "./Glossary";
import { Chevron } from "./ui";
import { DivBars, PlanVisual, RangeRows, TouchBar, Waterfall, ZoneScale } from "./Viz";

/** 판단 엔진의 매수·매도 기준 (backend/verdict.py BUY_TH) */
const BUY_TH = 0.15;
/** 기간별 눈금의 양 끝. 이보다 큰 점수는 끝에 붙인다 (실제 점수는 대부분 ±50 안쪽이라 ±100 눈금에서는 가운데로 몰린다) */
const TRACK = 0.6;
const trackX = (s: number) => ((Math.max(-TRACK, Math.min(TRACK, s)) + TRACK) / (2 * TRACK)) * 100;

export function VerdictHero({ r, horizon, onHorizon }: { r: Result; horizon: Horizon; onHorizon: (h: Horizon) => void }) {
  const v = r.verdicts[horizon];
  const tone = v.verdict === "buy" ? "up" : v.verdict === "sell" ? "down" : "hold";
  const split = new Set(HORIZONS.map((h) => r.verdicts[h].verdict)).size > 1;
  return (
    <section className={`card verdict tone-${tone}`} aria-labelledby="verdict-title">
      <div className="verdict-say">
        <h2 id="verdict-title" className="verdict-label" tabIndex={-1}>
          {v.strong ? "강한 " : ""}{v.label} <small>{HORIZON_LABEL[horizon]} 기준</small>
        </h2>
        <p className="verdict-headline">{r.narrative.headline}</p>
        <p className="verdict-summary">{r.narrative.summary}</p>
        <div className={`advice${r.advice.holding ? " holding" : ""}`}>
          <span className="advice-tag">{r.advice.holding ? `보유 중, 평가손익 ${pct(r.advice.pnl)}` : "미보유 기준"}</span>
          {r.advice.action && <b className="advice-action">{r.advice.action}</b>}
          <p>{r.advice.text}</p>
        </div>
      </div>
      <div className="hz">
        <h3>기간별 판단</h3>
        <div className="hz-rows" role="group" aria-label="판단 기간 바꾸기">
          {HORIZONS.map((h) => {
            const x = r.verdicts[h];
            return (
              <button key={h} type="button" aria-pressed={h === horizon} className={`hz-row v-${x.verdict}`} onClick={() => onHorizon(h)}
                      aria-label={`${HORIZON_LABEL[h]} ${x.strong ? "강한 " : ""}${x.label}, 종합 점수 ${signed(x.score * 100, 0)}`}>
                <span className="hz-name">{HORIZON_SHORT[h]}</span>
                <b>{x.strong ? "강한 " : ""}{x.label}</b>
                <span className="hz-track"><i /><i /><i /><span className="hz-pin" style={{ left: `${trackX(x.score)}%` }} /></span>
                <span className="num hz-score">{signed(x.score * 100, 0)}</span>
              </button>
            );
          })}
          <div className="hz-axis" aria-hidden="true"><span>매도</span><span>관망</span><span>매수</span></div>
        </div>
        {split && <p className="hz-note">짧게 볼수록 타이밍(추세, 뉴스, 시장)이, 길게 볼수록 회사(가치, 재무)가 크게 반영되어 기간마다 판단이 다릅니다.</p>}
        <dl className="verdict-stats">
          <div><dt><Term k="종합 점수" /></dt><dd className="num">{signed(v.score * 100, 0)}</dd></div>
          <div><dt><Term k="신뢰도" /></dt><dd className="num">{pct(v.confidence, 0, false)}</dd></div>
          <div><dt>근거 <Term k="일치도" /></dt><dd><span className="num">{pct(v.agreement, 0, false)}</span><small>{agreementWord(v.agreement)}</small></dd></div>
        </dl>
        {v.notes.length > 0 && <ul className="notes">{v.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
      </div>
    </section>
  );
}

/** 모듈 → 자세히 볼 탭 */
export const MODULE_TAB: Record<string, string> = {
  technical: "chart", strategies: "strategies", analogs: "simulation", sentiment: "news", consensus: "fundamentals",
  valuation: "valuation", quality: "fundamentals", personas: "personas", regime: "chart",
};

/** 지금 상황을 보는 두 축. 회사 = 무엇을 사는가, 타이밍 = 언제 사는가.
 *  판단 엔진의 기간 가중치도 이 축을 따른다 (단기는 타이밍 쪽, 장기는 회사 쪽 모듈이 크다: backend/verdict.py WEIGHTS). */
const GROUPS = [
  { key: "company", label: "회사", sub: "가치, 재무, 전문가 평가", mods: ["valuation", "quality", "consensus", "personas"], words: ["약함", "보통", "좋음"] },
  { key: "timing", label: "타이밍", sub: "추세, 뉴스, 시장 분위기", mods: ["technical", "strategies", "analogs", "sentiment", "regime"], words: ["나쁨", "보통", "좋음"] },
] as const;

/** 모듈 점수를 그 모듈의 말로: [매도 쪽, 중립, 매수 쪽]. 경계는 판단 엔진과 같은 ±0.15 */
const SIGNAL_WORD: Record<string, [string, string, string]> = {
  valuation: ["고평가", "적정", "저평가"], quality: ["약함", "보통", "탄탄"], consensus: ["부정적", "중립", "긍정적"],
  personas: ["부정적", "엇갈림", "긍정적"], technical: ["약세", "중립", "강세"], strategies: ["매도 신호", "신호 없음", "매수 신호"],
  analogs: ["약했음", "평소와 비슷", "강했음"], sentiment: ["부정적", "잠잠", "긍정적"], regime: ["불안", "보통", "우호적"],
};
const level = (s: number) => (s >= BUY_TH ? 2 : s <= -BUY_TH ? 0 : 1);
const LEVEL_CLS = ["down", "flat", "up"] as const;

/** 묶음 점수: 모듈 점수를 신뢰도로 가중 평균한다 (기간 가중치와 무관한 '지금 상황'). 쓸 수 있는 모듈이 없으면 null */
function groupScore(mods: ModuleCard[]): number | null {
  const ok = mods.filter((m) => m.available);
  const w = ok.reduce((a, m) => a + m.confidence, 0);
  return w > 0 ? ok.reduce((a, m) => a + m.confidence * m.score, 0) / w : null;
}

/** 회사(세로)와 타이밍(가로) 지도. 가운데 띠는 '보통'(±0.15), 점이 놓인 칸을 밝힌다 */
function SituationMap({ company, timing }: { company: number; timing: number }) {
  const pos = (s: number) => (Math.max(-1, Math.min(1, s)) + 1) * 50;
  return (
    <div className="sit-map" role="img"
         aria-label={`회사 ${GROUPS[0].words[level(company)]} ${signed(company * 100, 0)}, 타이밍 ${GROUPS[1].words[level(timing)]} ${signed(timing * 100, 0)}`}>
      {[2, 1, 0].flatMap((c) => [0, 1, 2].map((t) => (
        <span key={`${c}${t}`} className={`sit-cell${level(company) === c && level(timing) === t ? " on" : ""}`}>
          {c !== 1 && t !== 1 && <>{c ? "좋은 회사" : "약한 회사"}<br />{t ? "좋은 타이밍" : "나쁜 타이밍"}</>}
        </span>
      )))}
      <i className="sit-dot" style={{ left: `${pos(timing)}%`, top: `${100 - pos(company)}%` }} />
    </div>
  );
}

export function Situation({ r, onOpen }: { r: Result; onOpen: (tab: string) => void }) {
  const groups = GROUPS.map((g) => {
    const mods = g.mods.map((k) => r.modules.find((m) => m.key === k)).filter((m): m is ModuleCard => !!m);
    return { ...g, mods, score: groupScore(mods) };
  });
  const [company, timing] = groups.map((g) => g.score);
  return (
    <section className="card situation" aria-labelledby="sit-title">
      <div className="sit-over">
        <h2 id="sit-title">지금 상황</h2>
        <p className="sit-lead">
          {groups.filter((g) => g.score !== null).map((g, i) => (
            <span key={g.key}>{i > 0 && ", "}{g.label} <b className={LEVEL_CLS[level(g.score as number)]}>{g.words[level(g.score as number)]}</b></span>
          ))}
        </p>
        {company !== null && timing !== null && <SituationMap company={company} timing={timing} />}
      </div>
      {groups.map((g) => (
        <div className="sig-group" key={g.key}>
          <h3>{g.label}<small>{g.sub}</small>
            {g.score !== null && <span className={`num ${LEVEL_CLS[level(g.score)]}`}>{signed(g.score * 100, 0)}</span>}</h3>
          <ul>
            {g.mods.map((m) => {
              const lv = level(m.score);
              const word = m.available ? SIGNAL_WORD[m.key]?.[lv] ?? "" : "데이터 없음";
              return (
                <li key={m.key}>
                  <button type="button" className={`sig-row${m.available ? "" : " off"}`} title={m.summary} onClick={() => onOpen(MODULE_TAB[m.key] ?? "summary")}
                          aria-label={`${m.label}: ${word}, 점수 ${m.available ? signed(m.score * 100, 0) : "없음"}. ${m.summary} 자세히 보기`}>
                    <span className="sig-name">{m.label}</span>
                    <span className={`sig-word ${m.available ? LEVEL_CLS[lv] : "flat"}`}>{word}</span>
                    <ScoreBar score={m.score} muted={!m.available} />
                    <span className={`num sig-score ${m.available ? LEVEL_CLS[lv] : "flat"}`}>{m.available ? signed(m.score * 100, 0) : "-"}</span>
                    <Chevron />
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </section>
  );
}

/** 종합 점수가 만들어진 과정 (접어 둔다: 판단을 읽는 데는 필요 없고, 따져 볼 때만 편다) */
export function ScoreDetail({ r, horizon }: { r: Result; horizon: Horizon }) {
  const v = r.verdicts[horizon];
  // 판단에 많이 반영된 모듈부터, 판단에서 빠진 모듈은 맨 뒤. 키 순서에 기대지 않고 값으로 정렬한다
  // (저장된 결과는 PostgreSQL JSONB 를 거치며 키가 길이·글자순으로 다시 놓인다)
  const rank = (key: string) => (key in v.contributions ? -Math.abs(v.contributions[key]) : 1);
  const mods = [...r.modules].sort((a, b) => rank(a.key) - rank(b.key));
  return (
    <details className="card score-detail">
      <summary><h2>종합 점수 {signed(v.score * 100, 0)} 계산 과정</h2><Chevron /></summary>
      <p className="hint factor-help">점수는 모듈마다 -100(매도 쪽)부터 +100(매수 쪽)까지입니다. 반영은 점수에 {HORIZON_LABEL[horizon]} 가중치와 신뢰도를 곱한 값으로,
        모두 더하면 종합 점수입니다.</p>
      <Waterfall steps={mods.filter((m) => m.key in v.contributions).map((m) => ({ key: m.key, label: m.label, value: v.contributions[m.key] }))}
                 total={v.score} fmt={(x) => signed(x * 100, 1)}
                 marks={[{ at: -BUY_TH, label: `매도 기준 ${signed(-BUY_TH * 100, 0)}` }, { at: BUY_TH, label: `매수 기준 ${signed(BUY_TH * 100, 0)}` }]} />
      <div className="table-wrap"><table className="table compact">
        <caption className="sr-only">모듈별 점수, 가중치, 신뢰도, 반영</caption>
        <thead><tr><th>모듈</th><th>점수</th><th>가중치</th><th>신뢰도</th><th>반영</th></tr></thead>
        <tbody>
          {mods.map((m) => {
            const contrib = v.contributions[m.key];
            return (
              <tr key={m.key}>
                <th scope="row">{m.label}</th>
                <td className={`num ${dirClass(m.score)}`}>{m.available ? signed(m.score * 100, 0) : "-"}</td>
                <td className="num">{pct(v.weights[m.key] ?? 0, 0, false)}</td>
                <td className="num">{m.available ? pct(m.confidence, 0, false) : "-"}</td>
                <td className={`num ${dirClass(contrib, 0.0005)}`}>{contrib === undefined ? "-" : signed(contrib * 100, 1)}</td>
              </tr>
            );
          })}
        </tbody>
      </table></div>
    </details>
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
      <p className="hint wide">
        {r.meta.narrative_error ? `AI 해설을 만들지 못해 계량 결과로 대신했습니다 (${r.meta.narrative_error}).` : `해설은 AI(${(r.meta.llm_model ?? "").split("/").pop()})가 썼습니다.`}
        {" "}판단은 계량 엔진이 정하고, 해설은 판단을 바꾸지 못합니다.</p>
    </section>
  );
}

/** 요약 탭용 매매 계획: 시뮬레이션 핵심 수치, 먼저 닿을 확률, 가격 분포(손절·목표 선) */
export function PlanSummary({ r, horizon, onOpen }: { r: Result; horizon: Horizon; onOpen: () => void }) {
  const p = r.simulation.plans[horizon];
  const s = r.simulation.summary[horizon];
  const v = r.verdicts[horizon];
  // 관망·매도인데 보유하지 않았다면, 이 계획은 '그래도 산다면' 의 기준이다 (바로 사라는 뜻으로 읽히지 않게)
  const ifBuying = v.verdict !== "buy" && !r.advice.holding;
  return (
    <section className="card plan-summary" aria-labelledby="plan-sum-title">
      <div className="panel-head">
        <h2 id="plan-sum-title">{ifBuying ? "그래도 산다면" : "매매 계획"} <span className="sub">{HORIZON_LABEL[horizon]} 시뮬레이션</span></h2>
        <button className="btn outline" onClick={onOpen}>시뮬레이션 자세히<Chevron /></button>
      </div>
      {ifBuying && <p className="hint plan-note">지금은 {v.label} 구간입니다. 아래는 사기로 했을 때 쓸 기준입니다.</p>}
      <div className="plan-grid">
        <div className="stack">
          <dl className="kv">
            <div><dt>{HORIZON_SHORT[horizon]} 뒤 오를 확률</dt><dd className="num">{pct(s.p_up, 0, false)}</dd></div>
            <div><dt>예상 수익 (중앙값)</dt><dd className={`num ${dirClass(s.median, 0.001)}`}>{pct(s.median)}</dd></div>
            <div><dt>예상 범위 (90%)</dt><dd className="num">{pct(s.p5, 0)} ~ {pct(s.p95, 0)}</dd></div>
            <div><dt><Term k="손익비" /></dt><dd className="num">{num(p.reward_risk, 2)} : 1</dd></div>
          </dl>
          <TouchBar p={p} days={s.days} />
        </div>
        <FanChart fan={r.simulation.summary.fan} days={s.days} price={p.entry} stop={p.stop} target={p.target} />
      </div>
    </section>
  );
}

/** 가격 위치: 현재가를 가운데 0 으로 두고 주요 가격대가 얼마나 위·아래에 있는지. 높은 가격부터 놓는다 */
export function PriceLevels({ r, horizon }: { r: Result; horizon: Horizon }) {
  const price = r.quote.price, p = r.simulation.plans[horizon], lv = r.technical.levels;
  const upside = r.fundamentals.consensus.detail?.target_upside;
  const rows = ([
    ["목표가", p.target], ["손절가", p.stop], ["추정 적정가", r.fundamentals.valuation.fair_value],
    ["애널리스트 목표가", isNum(upside) ? price * (1 + upside) : null],
    // 예전에 저장된 결과는 이동평균 키가 SMA200, SMA50 이다
    ["200일선", lv["200일선"] ?? lv.SMA200], ["50일선", lv["50일선"] ?? lv.SMA50], ["52주 최고", lv["52주 최고"]], ["52주 최저", lv["52주 최저"]],
  ] as [string, number | null | undefined][]).filter((x): x is [string, number] => isNum(x[1])).sort((a, b) => b[1] - a[1]);
  return (
    <section className="card levels-map" aria-labelledby="lv-title">
      <h2 id="lv-title">가격 위치 <span className="sub">가운데 선이 현재가 {usd(price)}</span></h2>
      <DivBars label="현재가 대비 주요 가격대" fmt={(x) => pct(x, 0)}
               rows={rows.map(([name, v]) => ({ key: name, label: <>{name}<small className="num">{usd(v)}</small></>, value: v / price - 1 }))} />
    </section>
  );
}

export function PlanCard({ r, horizon }: { r: Result; horizon: Horizon }) {
  const p = r.simulation.plans[horizon];
  const s = r.simulation.summary;
  return (
    <section className="card plan" aria-labelledby="plan-title">
      <h2 id="plan-title">매매 계획과 몬테카를로 시뮬레이션 <span className="sub">{HORIZON_LABEL[horizon]}</span></h2>
      <div className="plan-grid">
        <div className="stack">
          <PlanVisual p={p} days={s[horizon].days} />
          <dl className="kv">
            <div><dt><Term k="손익비" /></dt><dd className="num">{num(p.reward_risk, 2)} : 1</dd></div>
            <div><dt>기대 수익 (시뮬)</dt><dd className={`num ${dirClass(p.expected_return, 0.001)}`}>{pct(p.expected_return)}</dd></div>
            <div><dt>계좌 대비 최대 비중</dt><dd className="num">{pct(p.max_weight, 0, false)}{p.kelly_half !== null && <small> (하프 켈리 {pct(p.kelly_half, 0, false)})</small>}</dd></div>
          </dl>
        </div>
        <div>
          <FanChart fan={s.fan} days={s[horizon].days} price={p.entry} stop={p.stop} target={p.target} />
          <p className="hint">손절과 목표 = <Term k="ATR" />({num(p.atr, 2)}) × {p.atr_mult[0]} / {p.atr_mult[1]}. 비중은 1회 손실을 계좌의 1%로 묶는 기준(최대 25%).
            경로 {r.simulation.paths.toLocaleString()}개, 최근 3년 수익률 블록 부트스트랩. 기대수익은 CAPM {pct(r.simulation.drift)} 로 맞춤(과거 추세를 미래로 늘리지 않음).</p>
        </div>
      </div>
      <h3>기간별 수익률 범위</h3>
      <RangeRows label="기간별 시뮬레이션 수익률 범위" rows={HORIZONS.map((h) => ({ key: h, label: HORIZON_LABEL[h], sim: s[h], on: h === horizon }))} />
      <p className="hint">가는 선은 시나리오 90%(5%~95%), 진한 상자는 가운데 50%(25%~75%), 세로 막대는 중앙값입니다.</p>
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

const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
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
  const vix = r.regime.detail?.vix;
  const rets = ([["ret_1m", "1개월"], ["ret_3m", "3개월"], ["ret_6m", "6개월"], ["ret_12m", "1년"], ["rel_strength_6m", "S&P500 대비 6개월"]] as const)
    .filter(([k]) => isNum(t[k])).map(([k, label]) => ({ key: k, label, value: t[k] as number }));
  return (
    <section className="card">
      <h2>추세와 시장 국면</h2>
      <div className="viz-grid">
        <div>
          <h3>기간별 수익률</h3>
          <DivBars label="기간별 수익률" fmt={(x) => pct(x)} max={Math.max(0.1, ...rets.map((x) => Math.abs(x.value ?? 0)))} rows={rets} />
        </div>
        <div>
          {isNum(t.rsi14) && <ZoneScale label="RSI(14)" value={t.rsi14} min={0} max={100} fmt={(x) => num(x, 0)}
                                        zones={[{ to: 30, label: "과매도" }, { to: 70, label: "중립" }, { to: 100, label: "과매수" }]} />}
          {isNum(t.adx) && <ZoneScale label="ADX(14) 추세 강도" value={t.adx} min={0} max={60} fmt={(x) => num(x, 0)}
                                      zones={[{ to: 20, label: "약함" }, { to: 40, label: "추세 있음" }, { to: 60, label: "강함" }]} />}
          {isNum(vix) && <ZoneScale label="VIX 시장 불안" value={vix} min={0} max={50} fmt={(x) => num(x, 0)}
                                    zones={[{ to: 15, label: "안정" }, { to: 25, label: "보통" }, { to: 35, label: "불안" }, { to: 50, label: "공포" }]} />}
        </div>
      </div>
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
