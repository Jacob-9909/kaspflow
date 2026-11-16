/**
 * 앱 셸: 상단 바(검색·기록·테마·로그인), 주소 기반 화면 전환, 종목 화면(시세·차트·실행·결과).
 *   /             홈
 *   /?s=NVDA      종목 화면 (실행 전)
 *   /?run=<id>    저장된 결과 (공유 가능한 주소)
 *   /?view=history 판단 기록
 * 라우터 라이브러리 없이 URLSearchParams + history API 로 처리한다.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { analyze, api, type Horizon, type Me, type Quote, type Result } from "./api";
import { PriceChart, type PriceLevel } from "./components/Charts";
import { History, Home, RunsTable } from "./components/History";
import { Analogs, Fundamentals, News, Personas, Strategies, Valuation } from "./components/Sections";
import { FactorGrid, PlanCard, Reasons, RiskRegime, VerdictHero } from "./components/Report";
import { Progress, RunPanel, type StepState } from "./components/Run";
import Search from "./components/Search";
import { big, date, dateTime, dirClass, pct, usd } from "./format";

type Route = { view: "home" | "symbol" | "history"; symbol?: string; run?: string };

function readRoute(): Route {
  const p = new URLSearchParams(location.search);
  if (p.get("view") === "history") return { view: "history" };
  if (p.get("run")) return { view: "symbol", run: p.get("run")! };
  if (p.get("s")) return { view: "symbol", symbol: p.get("s")!.toUpperCase() };
  return { view: "home" };
}

function routeUrl(r: Route) {
  if (r.view === "history") return "/?view=history";
  if (r.run) return `/?run=${r.run}`;
  if (r.symbol) return `/?s=${encodeURIComponent(r.symbol)}`;
  return "/";
}

function initialTheme(): string {
  try {
    return localStorage.getItem("kf-theme") ?? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  } catch {
    return "dark";
  }
}

const SECTIONS: [string, string][] = [["sec-summary", "요약"], ["sec-plan", "시뮬레이션"], ["sec-strategies", "전략 검증"],
  ["sec-valuation", "가치평가"], ["sec-fundamentals", "재무"], ["sec-personas", "투자 대가"], ["sec-news", "뉴스"]];

export default function App() {
  const [route, setRoute] = useState<Route>(readRoute);
  const [theme, setTheme] = useState(initialTheme);
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("kf-theme", theme); } catch { /* 저장 못 해도 동작에는 문제없다 */ }
  }, [theme]);

  const refreshMe = useCallback(() => { api.me().then(setMe).catch(() => setMe({ authenticated: false, login_enabled: false, llm: false })); }, []);
  useEffect(refreshMe, [refreshMe]);

  useEffect(() => {
    const onPop = () => setRoute(readRoute());
    addEventListener("popstate", onPop);
    return () => removeEventListener("popstate", onPop);
  }, []);

  const go = useCallback((r: Route, replace = false) => {
    const url = routeUrl(r);
    if (replace) history.replaceState(null, "", url);
    else if (url !== location.pathname + location.search) history.pushState(null, "", url);
    setRoute(r);
    if (!replace) scrollTo({ top: 0 });
  }, []);

  return (
    <>
      <a className="skip" href="#main">본문으로 건너뛰기</a>
      <header className="topbar">
        <button className="brand" onClick={() => go({ view: "home" })} aria-label="kaspflow 홈">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 17l5-6 4 4 8-10" /><path d="M14 5h6v6" /></svg>
          <span>kaspflow</span><small>매매 검증기</small>
        </button>
        <div className="topsearch"><Search onPick={(s) => go({ view: "symbol", symbol: s })} /></div>
        <nav className="topnav" aria-label="주 메뉴">
          <button className={route.view === "history" ? "on" : ""} onClick={() => go({ view: "history" })}>판단 기록</button>
          <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label={theme === "dark" ? "밝은 테마로" : "어두운 테마로"}>
            {theme === "dark"
              ? <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
              : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>}
          </button>
          {me?.authenticated && <button onClick={() => api.logout().then(refreshMe)}>로그아웃</button>}
        </nav>
      </header>
      <main id="main" className="container">
        {route.view === "home" && <Home onPick={(s) => go({ view: "symbol", symbol: s })} onOpen={(id) => go({ view: "symbol", run: id })} />}
        {route.view === "history" && <History onOpen={(id) => go({ view: "symbol", run: id })} />}
        {route.view === "symbol" && (
          <SymbolPage key={route.run ?? route.symbol} route={route} me={me} theme={theme} onLogin={refreshMe}
                      onRunSaved={(id) => go({ view: "symbol", run: id }, true)} onOpen={(id) => go({ view: "symbol", run: id })} />
        )}
      </main>
      <footer className="site-footer">
        <p>공개 데이터(Yahoo Finance, Google News)와 통계 모델로 만든 참고 자료이며 투자 권유가 아닙니다. 과거 성과와 시뮬레이션은 미래 수익을 보장하지 않습니다.</p>
      </footer>
    </>
  );
}

function SymbolPage({ route, me, theme, onLogin, onRunSaved, onOpen }: {
  route: Route; me: Me | null; theme: string; onLogin: () => void; onRunSaved: (id: string) => void; onOpen: (id: string) => void;
}) {
  const [quote, setQuote] = useState<Quote | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [steps, setSteps] = useState<StepState[] | null>(null);
  const [queued, setQueued] = useState(false);
  const [running, setRunning] = useState(false);
  const [horizon, setHorizon] = useState<Horizon>("mid");
  const [strategy, setStrategy] = useState("");
  const [past, setPast] = useState<Awaited<ReturnType<typeof api.runs>> | null>(null);
  const symbol = result?.symbol ?? route.symbol ?? quote?.symbol;

  useEffect(() => {
    setError(null);
    if (route.run) {
      api.run(route.run).then((r) => { setResult(r); setHorizon(r.horizon); }).catch((e) => setError(`기록을 불러오지 못했습니다: ${(e as Error).message}`));
    } else if (route.symbol) {
      api.quote(route.symbol).then(setQuote).catch((e) => setError((e as Error).message));
    }
  }, [route.run, route.symbol]);

  useEffect(() => {
    if (symbol) api.runs(symbol).then(setPast).catch(() => setPast(null));
  }, [symbol, result?.run_id]);

  useEffect(() => {
    if (!result) return;
    // 차트 화살표 기본값: 판단에 쓰인(가중치 큰) 비단타 전략. 변동성 돌파는 매일 매매라 화살표가 너무 많다.
    const ranked = [...result.validation.strategies].sort((a, b) => Number(a.family === "day") - Number(b.family === "day")
      || b.weight - a.weight || (b.wf?.metrics.sharpe ?? -9) - (a.wf?.metrics.sharpe ?? -9));
    setStrategy(ranked[0]?.key ?? "");
  }, [result]);

  const run = (h: Horizon, pos: { qty: number; avg: number } | null) => {
    if (!symbol) return;
    setRunning(true);
    setError(null);
    setResult(null);
    setSteps(null);
    analyze(symbol, h, pos, (ev) => {
      if (ev.type === "start") {
        setQueued(ev.queued);
        setSteps(ev.steps.map(([key, label]) => ({ key, label, status: "wait" })));
      } else if (ev.type === "step") {
        setQueued(false);
        setSteps((s) => s?.map((x) => x.key === ev.step ? { ...x, status: ev.status === "done" ? "done" : "running", detail: ev.detail ?? x.detail, ms: ev.ms } : x) ?? null);
      } else if (ev.type === "done") {
        setRunning(false);
        setResult({ ...ev.data, run_id: ev.run_id ?? undefined });
        setHorizon(ev.data.horizon);
        if (ev.run_id) onRunSaved(ev.run_id);
      } else {
        setRunning(false);
        setError(ev.message);
      }
    });
  };

  const sel = result?.validation.strategies.find((s) => s.key === strategy);
  const levels = useMemo<PriceLevel[]>(() => {
    if (!result) return [];
    const p = result.simulation.plans[horizon];
    return [{ price: p.stop, label: "손절", tone: "down" }, { price: p.target, label: "목표", tone: "up" }];
  }, [result, horizon]);

  const head = result ? { name: result.name, symbol: result.symbol, price: result.quote.price, change: result.quote.change,
    exchange: result.quote.exchange, sector: result.quote.sector, industry: result.quote.industry, cap: result.quote.market_cap, asOf: result.quote.as_of }
    : quote ? { name: quote.name, symbol: quote.symbol, price: quote.price, change: quote.change, exchange: quote.exchange,
      sector: quote.sector, industry: quote.industry, cap: quote.market_cap, asOf: quote.as_of } : null;

  return (
    <div className="symbol-page">
      {error && <div className="banner error" role="alert">{error}</div>}
      {head ? (
        <header className="sym-head">
          <div>
            <h1>{head.name} <span className="ticker">{head.symbol}</span></h1>
            <p className="hint">{[head.exchange, head.sector, head.industry].filter(Boolean).join(" · ")} · 시가총액 {big(head.cap)} · 기준일 {head.asOf}</p>
          </div>
          <div className="price-block">
            <span className="price num">{usd(head.price)}</span>
            <span className={`change num ${dirClass(head.change, 0)}`}>{pct(head.change, 2)}</span>
          </div>
          {quote && !result && (
            <div className="range52" aria-label="52주 범위">
              <span className="num">{usd(quote.low52)}</span>
              <div className="track"><div className="dot" style={{ left: `${((quote.price - quote.low52) / Math.max(quote.high52 - quote.low52, 1e-9)) * 100}%` }} /></div>
              <span className="num">{usd(quote.high52)}</span>
              <small>52주</small>
            </div>
          )}
        </header>
      ) : !error && <div className="skeleton head" aria-busy="true" />}

      <div className="layout">
        <section className="card chart-card" aria-label="가격 차트">
          <div className="chart-legend"><span className="lg sma50">SMA50</span><span className="lg sma200">SMA200</span>
            {sel && <span className="lg marks">{sel.label} 매매 (최근 2년)</span>}{result && <span className="lg lv">손절 · 목표 ({horizon === "short" ? "단기" : horizon === "mid" ? "중기" : "장기"})</span>}</div>
          {result ? <PriceChart candles={result.chart.candles} sma50={result.chart.sma50} sma200={result.chart.sma200} markers={sel?.markers} levels={levels} theme={theme} />
            : quote ? <PriceChart candles={quote.candles} sma50={quote.sma50} sma200={quote.sma200} theme={theme} /> : <div className="skeleton chart" />}
          {result?.quote.partial_bar && <p className="hint">장중 데이터: 마지막 봉은 아직 진행 중이라 검증·백테스트에서 뺐습니다.</p>}
        </section>
        <aside className="side">
          {(quote || result) && (
            <RunPanel authed={!!me?.authenticated} loginEnabled={!!me?.login_enabled} running={running} onRun={run} onLogin={onLogin} />
          )}
          {steps && (running || !result) && <Progress steps={steps} queued={queued} />}
          {result && (
            <section className="card meta">
              <h2>이 결과</h2>
              <p className="hint">{result.created_at ? `${dateTime(result.created_at)} 실행` : "방금 실행"} · {((result.meta.duration_ms ?? 0) / 1000).toFixed(1)}초 · LLM {result.meta.llm_model ?? "-"} ({result.meta.llm_calls ?? 0}회)
                {result.position && ` · 보유 ${result.position.qty}주 @ ${usd(result.position.avg_price)}`}</p>
              <button className="btn ghost" onClick={() => navigator.clipboard?.writeText(location.href)}>결과 주소 복사</button>
            </section>
          )}
        </aside>
      </div>

      {result && (
        <>
          <nav className="subnav" aria-label="결과 섹션">{SECTIONS.map(([id, label]) => <a key={id} href={`#${id}`}>{label}</a>)}</nav>
          <div id="sec-summary" className="stack">
            <VerdictHero r={result} horizon={horizon} onHorizon={setHorizon} />
            <FactorGrid r={result} horizon={horizon} />
            <Reasons r={result} />
          </div>
          <div id="sec-plan" className="stack">
            <PlanCard r={result} horizon={horizon} />
            <Analogs r={result} horizon={horizon} />
            <RiskRegime r={result} />
          </div>
          <Strategies r={result} selected={strategy} onSelect={setStrategy} theme={theme} />
          <Valuation r={result} />
          <Fundamentals r={result} />
          <Personas r={result} />
          <News r={result} />
          <p className="disclaimer">{result.meta.disclaimer} 데이터 기준 {date(result.as_of)}.</p>
        </>
      )}

      {past && past.runs.length > 0 && (
        <section className="card">
          <h2>{symbol} 판단 기록</h2>
          <RunsTable data={past} onOpen={onOpen} compact />
        </section>
      )}
    </div>
  );
}
