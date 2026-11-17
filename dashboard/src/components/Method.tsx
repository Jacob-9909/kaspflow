/** 검증 방법 페이지: 판단이 어떻게 나오는지 한 화면에서 설명한다 (docs/methodology.md 의 요약). */
import type { Horizon, Verdict } from "../api";
import bt from "../engine-backtest.json";
import { GRADE_INFO, HORIZON_LABEL, HORIZONS, VERDICT_LABEL, pct, signed } from "../format";
import { GLOSSARY } from "./Glossary";

interface VStat { n: number; hit: number; base: number; avg: number }
interface HStat { n: number; avg: number; by_verdict: Partial<Record<Verdict, VStat>>; by_conf: { lo: number; hi: number; n: number; hit: number; base: number }[] }
const H = bt.horizons as Record<Horizon, HStat>;
const VERDICTS: Verdict[] = ["buy", "hold", "sell"];

/** 결과(JSON)에서 결론 문장을 만든다. 다시 계산하면 문장도 따라 바뀐다. */
function conclusions(): string[] {
  const cells = HORIZONS.flatMap((h) => VERDICTS.map((v) => ({ h, v, x: H[h].by_verdict[v] })).filter((c): c is { h: Horizon; v: Verdict; x: VStat } => !!c.x));
  const name = (c: { h: Horizon; v: Verdict }) => `${HORIZON_LABEL[c.h]} ${VERDICT_LABEL[c.v]}`;
  const better = cells.filter((c) => c.x.hit - c.x.base >= 0.03);
  const worse = cells.filter((c) => c.x.hit - c.x.base <= -0.03);
  const out = [better.length
    ? `기준선보다 3%p 넘게 잘 맞은 곳: ${better.map((c) => `${name(c)}(${signed((c.x.hit - c.x.base) * 100, 1)}%p)`).join(", ")}.`
    : "어느 기간, 어느 판단도 기준선보다 3%p 넘게 잘 맞지 않았습니다. 이 세 모듈만으로는 아직 예측력이 확인되지 않았습니다."];
  if (worse.length) out.push(`기준선보다 3%p 넘게 덜 맞은 곳: ${worse.map((c) => `${name(c)}(${signed((c.x.hit - c.x.base) * 100, 1)}%p)`).join(", ")}.`);
  if (HORIZONS.every((h) => (H[h].by_verdict.sell?.avg ?? -1) > H[h].avg))
    out.push("세 기간 모두 매도 판단 뒤 주가가 전체 평균보다 더 올랐습니다. 이 모듈들의 매도 신호는 거꾸로 맞았습니다.");
  if (HORIZONS.every((h) => { const c = H[h].by_conf; return c.length > 1 && c[c.length - 1].hit - c[c.length - 1].base <= c[0].hit - c[0].base; }))
    out.push("신뢰도가 높은 판단이 더 잘 맞지도 않았습니다.");
  return out;
}

function EngineRecord() {
  return (
    <section className="card" aria-labelledby="record-title">
      <h2 id="record-title">판단 엔진의 과거 성적 <span className="sub">부분 검증</span></h2>
      <p>대형주 {bt.symbols.length}개를 {bt.start.slice(0, 7)}부터 {bt.end.slice(0, 7)}까지 매달 한 번, 그 시점까지의 데이터만 써서 다시 판단하고
        1개월, 3개월, 1년 뒤 결과와 맞춰 봤습니다. 적중 기준은 판단 기록과 같습니다(매수 +2% 초과, 매도 -2% 미만, 관망 ±5% 이내).
        기준선은 같은 기간에 늘 그 판단을 했을 때의 적중률입니다.</p>
      <ul className="record-conclusion">{conclusions().map((x) => <li key={x}>{x}</li>)}</ul>
      <div className="table-wrap"><table className="table compact">
        <caption className="sr-only">기간·판단별 과거 적중률과 기준선</caption>
        <thead><tr><th>기간</th><th>판단</th><th>판단 수</th><th>적중률</th><th>기준선</th><th>차이</th><th>이후 평균 수익률</th></tr></thead>
        <tbody>
          {HORIZONS.flatMap((h) => VERDICTS.map((v, i) => {
            const x = H[h].by_verdict[v];
            if (!x) return null;
            return (
              <tr key={h + v}>
                {i === 0 ? <th scope="row" rowSpan={3}>{HORIZON_LABEL[h]}<small className="record-avg">전체 평균 {pct(H[h].avg)}</small></th> : null}
                <td className="l"><span className={`vbadge v-${v}`}>{VERDICT_LABEL[v]}</span></td>
                <td className="num">{x.n.toLocaleString()}</td>
                <td className="num">{pct(x.hit, 1, false)}</td>
                <td className="num">{pct(x.base, 1, false)}</td>
                <td className="num">{signed((x.hit - x.base) * 100, 1)}%p</td>
                <td className="num">{pct(x.avg)}</td>
              </tr>
            );
          }))}
        </tbody>
      </table></div>
      <div className="table-wrap"><table className="table compact">
        <caption className="sr-only">신뢰도 구간별 적중률</caption>
        <thead><tr><th>기간</th>{H.short.by_conf.map((c) => <th key={c.lo}>신뢰도 {c.lo === 0 ? `${c.hi * 100}% 미만` : c.hi >= 1 ? `${c.lo * 100}% 이상` : `${c.lo * 100}~${c.hi * 100}%`}</th>)}</tr></thead>
        <tbody>
          {HORIZONS.map((h) => (
            <tr key={h}><th scope="row">{HORIZON_LABEL[h]}</th>
              {H[h].by_conf.map((c) => <td key={c.lo} className="num">{pct(c.hit, 1, false)} <small>기준선 {pct(c.base, 1, false)}</small></td>)}</tr>
          ))}
        </tbody>
      </table></div>
      <p className="hint">쓴 모듈: {bt.modules.join(", ")}. 판단 가중치로 단기의 {pct(bt.weight_share.short, 0, false)}, 중기의 {pct(bt.weight_share.mid, 0, false)},
        장기의 {pct(bt.weight_share.long, 0, false)}입니다. 재무, 밸류에이션, 애널리스트, 뉴스, 투자 대가는 과거 시점 값이 없어 뺐고, 검증된 전략 신호는 시점마다
        워크포워드 검증을 다시 돌려야 해서 뺐습니다. 지금의 대형주를 골랐으니 생존 편향이 있고, 1년 기간은 표본 창이 겹쳐 실제 독립 표본이 적습니다.
        결과 화면의 판단은 이 세 모듈에 나머지를 더한 것이라 이 성적과 같지 않습니다. 실전 성적은 판단 기록에 쌓입니다.
        ({bt.generated_at.slice(0, 10)} 계산, <code>backend/engine_backtest.py</code>)</p>
    </section>
  );
}

const STEPS: [string, string][] = [
  ["데이터 수집", "10년 일봉(배당과 분할 조정), 재무제표, 애널리스트 의견, 최근 3주 뉴스, S&P500(SPY), VIX, 금리, 섹터 ETF"],
  ["기술적 분석", "추세(50일선, 200일선, ADX), 모멘텀, S&P500 대비 상대강도, RSI, MACD, 거래량"],
  ["전략 검증", "전략 11개 × 변형 30개를 다음 날 시가 체결로 백테스트하고 워크포워드, 순열 검정, DSR 로 걸러 낸다"],
  ["시뮬레이션", "최근 3년 수익률을 블록 단위로 다시 뽑아 2,000개 가격 경로, 손절과 목표 도달 확률"],
  ["재무·가치평가", "수익성, 성장성, 건전성, DCF, 역DCF, PEG, 그레이엄, 애널리스트 컨센서스"],
  ["뉴스 감성", "기사 제목을 NIM 으로 분류해 관련도 × 영향도 × 최신성으로 가중"],
  ["투자 대가", "버핏, 그레이엄, 린치, 멍거, 캐시 우드, 드러켄밀러의 체크리스트와 AI 의견"],
  ["종합 판단", "기간별 가중치로 합산. 계량 엔진이 판단을 정하고 AI 는 이유만 설명한다"],
];

const WEIGHTS: [string, number, number, number][] = [
  ["기술적 지표", 22, 12, 4], ["검증된 전략 신호", 22, 15, 5], ["과거 유사 국면", 8, 6, 2], ["뉴스 감성", 16, 10, 4],
  ["애널리스트 컨센서스", 6, 12, 10], ["밸류에이션", 4, 15, 28], ["재무 품질", 2, 12, 25], ["투자 대가 패널", 5, 10, 18], ["시장 국면", 15, 8, 4],
];

const GRADE_RULE = ["순열 p ≤ 0.05, 샤프 ≥ 0.5, 거래 ≥ 15, DSR ≥ 0.8", "p ≤ 0.10, 샤프 ≥ 0.3, 거래 ≥ 8, DSR ≥ 0.5",
  "p ≤ 0.25, 샤프 > 0, 거래 ≥ 5", "위 조건을 하나도 채우지 못함"];

export default function Method() {
  return (
    <div className="method">
      <section className="hero left">
        <h1>판단은 이렇게 나옵니다</h1>
        <p>원칙은 세 가지입니다. 계산은 코드가 하고 AI 는 설명만 합니다. 통계 검증을 통과한 신호만 씁니다. 근거가 약하거나 엇갈리면 관망으로 기웁니다.</p>
      </section>

      <section className="card">
        <h2>8단계</h2>
        <ol className="steps">
          {STEPS.map(([t, d], i) => <li key={t}><span className="n">{i + 1}</span><div><b>{t}</b><p>{d}</p></div></li>)}
        </ol>
      </section>

      <section className="two-col">
        <div className="card">
          <h2>종합 점수와 판단</h2>
          <p className="formula">S = Σ (가중치 × 신뢰도 × 점수) ÷ Σ 가중치</p>
          <ul>
            <li>각 모듈 점수는 -100(매도 쪽) ~ +100(매수 쪽), 신뢰도는 0 ~ 100%</li>
            <li>S가 +15 이상이면 매수, -15 이하면 매도, 그 사이는 관망. ±35 를 넘으면 '강한'</li>
            <li>모듈 판정도 같은 경계를 쓴다: +40 이상 강한 매수 근거, +15 이상 매수 근거, -15~+15 중립, -15 이하 매도 근거, -40 이하 강한 매도 근거</li>
            <li>실적 발표 10일 이내, 연 변동성 60% 초과면 신뢰도를 낮춘다</li>
            <li>판단 기간이 지나면 기록 화면에서 적중을 매긴다 (매수 +2% 초과, 매도 -2% 미만, 관망 ±5% 이내)</li>
          </ul>
        </div>
        <div className="card">
          <h2>기간별 가중치 (%)</h2>
          <table className="table compact">
            <thead><tr><th>모듈</th><th>단기 1개월</th><th>중기 3개월</th><th>장기 1년</th></tr></thead>
            <tbody>{WEIGHTS.map(([m, s, mi, l]) => <tr key={m}><th scope="row">{m}</th><td>{s}</td><td>{mi}</td><td>{l}</td></tr>)}</tbody>
          </table>
          <p className="hint">단기는 가격 흐름과 뉴스, 장기는 가치와 재무 품질이 판단을 이끈다.</p>
        </div>
      </section>

      <EngineRecord />

      <section className="card">
        <h2>전략 검증 등급</h2>
        <p>A~F 등급은 전략 검증에만 쓴다. 전략이 과거에 낸 성과가 우연이 아닌지를 매기고, 등급이 높을수록 그 전략의 지금 신호를 판단에 크게 반영한다.
          결과 요약의 '매수 근거', '매도 근거' 표시는 모듈 점수의 방향이라 등급과 다르다.</p>
        <div className="table-wrap"><table className="table compact text">
          <thead><tr><th>등급</th><th>뜻</th><th>판단 반영</th><th>조건 (모두 표본 밖 성과)</th></tr></thead>
          <tbody>
            {GRADE_INFO.map(([g, meaning, use], i) => (
              <tr key={g}><th scope="row"><span className={`grade g-${g}`}>{g}</span></th><td>{meaning}</td><td>{use}</td><td>{GRADE_RULE[i]}</td></tr>
            ))}
          </tbody>
        </table></div>
        <p className="hint">단일 종목 10년 일봉에서는 대부분의 타이밍 전략이 우연과 구별되지 않는다. 그럴 때 이 모듈은 판단에서 빠진다.</p>
      </section>

      <section className="card">
        <h2>용어</h2>
        <dl className="glossary">{Object.entries(GLOSSARY).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      </section>

      <section className="card">
        <h2>데이터와 한계</h2>
        <ul>
          <li>시세, 재무, 애널리스트: Yahoo Finance (개인 용도). 뉴스: Google News RSS. AI: NVIDIA NIM.</li>
          <li>재무제표는 최근 4~5년뿐이라 10년 평균 같은 장기 지표는 보지 못한다.</li>
          <li>뉴스는 제목만 본다. 본문, 공시, 실적 발표 내용은 반영하지 않는다.</li>
          <li>세금, 환율(원/달러), 대량 주문의 시장 충격은 반영하지 않는다.</li>
          <li>투자 대가 패널은 실제 인물의 견해가 아니라 그 철학을 흉내 낸 규칙과 AI 해석이다.</li>
          <li>참고 자료이며 투자 권유가 아니다. 과거 성과와 시뮬레이션은 미래 수익을 보장하지 않는다.</li>
        </ul>
      </section>
    </div>
  );
}
