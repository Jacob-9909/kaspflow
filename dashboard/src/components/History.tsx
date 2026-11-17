/** 홈(검색·소개·최근 판단)과 판단 기록(적중률). 판단 기록이 곧 이 검증기 자체의 사후 검증이다. */
import { useEffect, useState } from "react";
import { api, type RunsResponse, type Verdict, type WatchItem, type WatchResponse } from "../api";
import { HORIZON_LABEL, HORIZON_SHORT, VERDICT_LABEL, ago, dateTime, dirClass, pct, signed } from "../format";
import Search from "./Search";
import { Chevron } from "./ui";

const POPULAR = ["NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "GOOGL", "META", "AMD", "PLTR", "SPY", "QQQ", "BTC-USD"];
const MODULE_INFO: [string, string][] = [
  ["기술적 지표", "추세(50일선, 200일선, ADX), 모멘텀, 상대강도, RSI, MACD"],
  ["검증된 전략 신호", "11개 전략 30개 변형을 워크포워드, 순열 검정, DSR 로 검증하고 통과한 것만 투표"],
  ["과거 유사 국면", "지금과 같은 추세, 모멘텀, RSI 구간이었던 날들의 이후 수익률"],
  ["몬테카를로", "2,000개 가격 경로로 범위, 손절과 목표 도달 확률, 비중"],
  ["밸류에이션", "DCF, 역DCF, PEG, 그레이엄, EPV, WACC 민감도"],
  ["재무 품질", "수익성, 성장성, 건전성, 피오트로스키 F, 알트만 Z"],
  ["애널리스트", "투자의견, 목표가 괴리, EPS 추정치 상향과 하향"],
  ["뉴스 감성", "최근 3주 기사를 NIM 으로 분류해 시간 가중"],
  ["투자 대가 6인", "버핏, 그레이엄, 린치, 멍거, 캐시 우드, 드러켄밀러 관점"],
];

export function RunsTable({ data, onOpen, compact = false }: { data: RunsResponse; onOpen: (id: string) => void; compact?: boolean }) {
  if (!data.runs.length) return <p className="hint">아직 실행 기록이 없습니다.</p>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr><th>실행 시각</th><th>종목</th><th>기간</th><th>판단</th><th>점수</th>{!compact && <th>요약</th>}<th>이후 수익률</th><th>평가</th></tr>
        </thead>
        <tbody>
          {data.runs.map((r) => (
            <tr key={r.id} className="clickable" tabIndex={0} onClick={() => onOpen(r.id)}
                onKeyDown={(e) => { if (e.key === "Enter") onOpen(r.id); }}>
              <td className="num">{dateTime(r.created_at)}{r.source === "auto" && <span className="tag">자동</span>}</td>
              <th scope="row"><span className="strat-name">{r.symbol}</span><div className="origin">{r.name}</div></th>
              <td>{HORIZON_LABEL[r.horizon]}</td>
              <td><span className={`vbadge v-${r.verdict}`}>{VERDICT_LABEL[r.verdict]}</span></td>
              <td className={`num ${dirClass(r.score)}`}>{signed(r.score * 100, 0)}</td>
              {!compact && <td className="headline">{r.headline ?? ""}</td>}
              <td className={`num ${dirClass(r.return, 0.001)}`}>{pct(r.return)}</td>
              <td>{r.evaluated ? (r.hit === null ? "-" : r.hit ? <span className="tag up">적중</span> : <span className="tag down">빗나감</span>)
                : <span className="tag">진행 중</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Stats({ data }: { data: RunsResponse }) {
  const s = data.stats;
  return (
    <div className="stats">
      <div><span className="k">평가 완료</span><b className="num">{s.evaluated}건</b></div>
      <div><span className="k">전체 적중률</span><b className="num">{s.hit_rate === null ? "-" : pct(s.hit_rate, 0, false)}</b></div>
      {(["buy", "hold", "sell"] as Verdict[]).map((v) => s.by_verdict[v] && (
        <div key={v}><span className="k">{VERDICT_LABEL[v]} 판단</span><b className="num">{pct(s.by_verdict[v]!.hit_rate, 0, false)}</b>
          <small className={dirClass(s.by_verdict[v]!.avg_return, 0.001)}>평균 {pct(s.by_verdict[v]!.avg_return)}, {s.by_verdict[v]!.n}건</small></div>
      ))}
    </div>
  );
}

const changed = (x: WatchItem) => !!(x.last && x.prev && x.last.verdict !== x.prev.verdict);

/** 관심 종목: 평일 장 마감 뒤 자동 판단한 최근 결과. 판단이 바뀐 종목을 맨 위에 둔다. */
function Watchlist({ authed, onPick, onOpen }: { authed: boolean; onPick: (s: string) => void; onOpen: (id: string) => void }) {
  const [w, setW] = useState<WatchResponse | null>(null);
  useEffect(() => { api.watchlist().then(setW).catch(() => setW(null)); }, []);
  if (!w || (!w.items.length && !authed)) return null;
  const rows = [...w.items].sort((a, b) => Number(changed(b)) - Number(changed(a)));
  return (
    <section className="card" aria-labelledby="watch-title">
      <h2 id="watch-title">관심 종목 <span className="sub">평일 미국 장 마감 뒤 하루 한 번 자동으로 판단합니다. 다음 판단 {dateTime(w.next_run)}</span></h2>
      {!rows.length ? <p className="hint">종목 화면에서 '관심 종목 추가'를 누르면 여기에 모입니다 (최대 {w.max}개). 판단이 바뀐 종목이 맨 위에 옵니다.</p> : (
        <ul className="watch-list">
          {rows.map((x) => (
            <li key={x.symbol}>
              <button type="button" className="watch-row" onClick={() => (x.last ? onOpen(x.last.id) : onPick(x.symbol))}
                      aria-label={`${x.symbol} ${HORIZON_LABEL[x.horizon]} ${x.last ? VERDICT_LABEL[x.last.verdict] : "첫 판단 전"}${changed(x) ? `, ${VERDICT_LABEL[x.prev!.verdict]}에서 바뀜` : ""}`}>
                <span className="watch-sym"><b>{x.symbol}</b><small>{x.name ?? ""}</small></span>
                <span className="watch-hz">{HORIZON_SHORT[x.horizon]}</span>
                <span className="watch-verdict">
                  {x.last ? <span className={`vbadge v-${x.last.verdict}`}>{VERDICT_LABEL[x.last.verdict]}</span> : <span className="hint">첫 판단 전</span>}
                  {changed(x) && <span className="tag">{VERDICT_LABEL[x.prev!.verdict]}에서 바뀜</span>}
                </span>
                <span className={`num watch-score ${dirClass(x.last?.score)}`}>{x.last ? signed(x.last.score * 100, 0) : ""}</span>
                <span className="hint watch-time">{x.last ? ago(x.last.created_at) : ""}</span>
                <Chevron />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function Home({ authed, onPick, onOpen }: { authed: boolean; onPick: (s: string) => void; onOpen: (id: string) => void }) {
  const [data, setData] = useState<RunsResponse | null>(null);
  useEffect(() => { api.runs().then(setData).catch(() => setData({ runs: [], stats: { evaluated: 0, hit_rate: null, by_verdict: {} } })); }, []);
  return (
    <div className="home">
      <section className="hero">
        <h1>지금 사야 할까, 팔아야 할까, 기다려야 할까</h1>
        <p>미국 주식 하나를 고르면 기술적 신호부터 전략 백테스트의 통계 검증, 몬테카를로 시뮬레이션, 가치평가, 뉴스, 투자 대가 관점까지 한 번에 돌려 기간별 판단을 냅니다.</p>
        <Search onPick={onPick} big autoFocus />
        <div className="chips">{POPULAR.map((s) => <button key={s} className="chip" onClick={() => onPick(s)}>{s}</button>)}</div>
      </section>
      <Watchlist authed={authed} onPick={onPick} onOpen={onOpen} />
      <section className="modules-info" aria-labelledby="modules-title">
        <h2 id="modules-title" className="sr-only">검증 항목</h2>
        {MODULE_INFO.map(([t, d]) => <div key={t} className="card mini"><h3>{t}</h3><p>{d}</p></div>)}
      </section>
      <section className="card">
        <h2>최근 판단 <span className="sub">판단 뒤 기간이 지나면 midas-touch 판단 원장과 같은 기준으로 적중을 매깁니다 (매수 +2% 초과, 매도 -2% 미만, 관망 ±5% 이내)</span></h2>
        {data ? <><Stats data={data} /><RunsTable data={{ ...data, runs: data.runs.slice(0, 10) }} onOpen={onOpen} compact /></> : <p className="hint">불러오는 중…</p>}
      </section>
    </div>
  );
}

export function History({ onOpen }: { onOpen: (id: string) => void }) {
  const [data, setData] = useState<RunsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.runs().then(setData).catch((e) => setErr((e as Error).message)); }, []);
  return (
    <section className="card">
      <h2>판단 기록과 적중률</h2>
      {err && <p className="field-error" role="alert">기록을 불러오지 못했습니다: {err}</p>}
      {data ? <><Stats data={data} /><RunsTable data={data} onOpen={onOpen} /></> : !err && <p className="hint">불러오는 중…</p>}
    </section>
  );
}
