/** 검증 방법 페이지: 판단이 어떻게 나오는지 한 화면에서 설명한다 (docs/methodology.md 의 요약). */
import { GLOSSARY } from "./Glossary";

const STEPS: [string, string][] = [
  ["데이터 수집", "10년 일봉(배당·분할 조정), 재무제표, 애널리스트 의견, 최근 3주 뉴스, SPY·VIX·금리·섹터 ETF"],
  ["기술적 분석", "추세(SMA50/200, ADX), 모멘텀, SPY 대비 상대강도, RSI, MACD, 거래량"],
  ["전략 검증", "전략 11개 x 변형 30개를 다음 날 시가 체결로 백테스트하고 워크포워드·순열 검정·DSR 로 걸러 낸다"],
  ["시뮬레이션", "최근 3년 수익률을 블록 단위로 다시 뽑아 2,000개 가격 경로, 손절·목표 도달 확률"],
  ["재무·가치평가", "수익성·성장성·건전성, DCF·역DCF·PEG·그레이엄, 애널리스트 컨센서스"],
  ["뉴스 감성", "기사 제목을 NIM 으로 분류해 관련도 x 영향도 x 최신성으로 가중"],
  ["투자 대가", "버핏·그레이엄·린치·멍거·캐시 우드·드러켄밀러 체크리스트 + AI 의견"],
  ["종합 판단", "기간별 가중치로 합산. 계량 엔진이 판단을 정하고 AI 는 이유만 설명한다"],
];

const WEIGHTS: [string, number, number, number][] = [
  ["기술적 지표", 22, 12, 4], ["검증된 전략 신호", 22, 15, 5], ["과거 유사 국면", 8, 6, 2], ["뉴스 감성", 16, 10, 4],
  ["애널리스트 컨센서스", 6, 12, 10], ["밸류에이션", 4, 15, 28], ["재무 품질", 2, 12, 25], ["투자 대가 패널", 5, 10, 18], ["시장 국면", 15, 8, 4],
];

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
          <p className="formula">S = Σ (가중치 x 신뢰도 x 점수) ÷ Σ 가중치</p>
          <ul>
            <li>각 모듈 점수는 -1(매도) ~ +1(매수), 신뢰도는 0 ~ 1</li>
            <li>S ≥ +0.15 매수 · S ≤ -0.15 매도 · 그 사이 관망 · |S| ≥ 0.35 면 '강한'</li>
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

      <section className="card">
        <h2>전략 검증 등급</h2>
        <table className="table compact">
          <thead><tr><th>등급</th><th>판단 가중치</th><th>조건 (모두 표본 밖 성과)</th></tr></thead>
          <tbody>
            <tr><th scope="row"><span className="grade g-A">A</span></th><td>1.0</td><td>순열 p ≤ 0.05, 샤프 ≥ 0.5, 거래 ≥ 15, DSR ≥ 0.8</td></tr>
            <tr><th scope="row"><span className="grade g-B">B</span></th><td>0.6</td><td>p ≤ 0.10, 샤프 ≥ 0.3, 거래 ≥ 8, DSR ≥ 0.5</td></tr>
            <tr><th scope="row"><span className="grade g-C">C</span></th><td>0.25</td><td>p ≤ 0.25, 샤프 &gt; 0, 거래 ≥ 5</td></tr>
            <tr><th scope="row"><span className="grade g-F">F</span></th><td>0</td><td>그 외 (판단에 쓰지 않음)</td></tr>
          </tbody>
        </table>
        <p className="hint">단일 종목 10년 일봉에서는 대부분의 타이밍 전략이 우연과 구별되지 않는다. 그럴 때 이 모듈은 판단에서 빠진다.</p>
      </section>

      <section className="card">
        <h2>용어</h2>
        <dl className="glossary">{Object.entries(GLOSSARY).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      </section>

      <section className="card">
        <h2>데이터와 한계</h2>
        <ul>
          <li>시세·재무·애널리스트: Yahoo Finance (개인 용도). 뉴스: Google News RSS. AI: NVIDIA NIM.</li>
          <li>재무제표는 최근 4~5년뿐이라 10년 평균 같은 장기 지표는 보지 못한다.</li>
          <li>뉴스는 제목만 본다. 본문·공시·실적 발표 내용은 반영하지 않는다.</li>
          <li>세금, 환율(원/달러), 대량 주문의 시장 충격은 반영하지 않는다.</li>
          <li>투자 대가 패널은 실제 인물의 견해가 아니라 그 철학을 흉내 낸 규칙과 AI 해석이다.</li>
          <li>참고 자료이며 투자 권유가 아니다. 과거 성과와 시뮬레이션은 미래 수익을 보장하지 않는다.</li>
        </ul>
      </section>
    </div>
  );
}
