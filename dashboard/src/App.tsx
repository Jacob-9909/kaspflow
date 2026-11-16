/**
 * 앱 셸: 전역 메뉴(검색·판단 기록·검증 방법·테마·로그인)와 주소 기반 화면 전환.
 *   /                      홈
 *   /?s=NVDA&tab=chart     종목 화면 (탭: 요약·차트·전략 검증·시뮬레이션·가치평가·재무·투자 대가·뉴스·판단 기록)
 *   /?run=<id>&tab=...     저장된 결과 (공유 가능한 주소)
 *   /?view=history         판단 기록 · 적중률
 *   /?view=method          검증 방법
 * 라우터 라이브러리 없이 URLSearchParams + history API 로 처리한다. 탭 전환은 replaceState (뒤로 가기가 탭마다 쌓이지 않게).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { analyze, api, type Horizon, type Me, type Quote, type Result, type RunsResponse } from "./api";
import { PriceChart, type PriceLevel } from "./components/Charts";
import { History, Home, RunsTable } from "./components/History";
import Method from "./components/Method";
import { FactorGrid, PlanCard, PlanSummary, Reasons, RiskCard, TrendCard, VerdictHero } from "./components/Report";
import { Progress, RunPanel, type StepState } from "./components/Run";
import Search, { rememberSymbol } from "./components/Search";
import { Analogs, Fundamentals, News, Personas, Strategies, Valuation } from "./components/Sections";
import { TabBar, type TabDef } from "./components/Tabs";
import { MetaList } from "./components/ui";
import { big, date, dateTime, dirClass, pct, usd } from "./format";

type View = "home" | "symbol" | "history" | "method";
type Route = { view: View; symbol?: string; run?: string; tab?: string };

function readRoute(): Route {
  const p = new URLSearchParams(location.search);
  const tab = p.get("tab") ?? undefined;
  if (p.get("view") === "history") return { view: "history" };
  if (p.get("view") === "method") return { view: "method" };
  if (p.get("run")) return { view: "symbol", run: p.get("run")!, tab };
  if (p.get("s")) return { view: "symbol", symbol: p.get("s")!.toUpperCase(), tab };
  return { view: "home" };
}

function routeUrl(r: Route) {
  if (r.view === "history") return "/?view=history";
  if (r.view === "method") return "/?view=method";
  const q = new URLSearchParams();
  if (r.run) q.set("run", r.run);
  else if (r.symbol) q.set("s", r.symbol);
  if (r.tab && r.tab !== "summary") q.set("tab", r.tab);
  const s = q.toString();
  return s ? `/?${s}` : "/";
}

function initialTheme(): string {
  try {
    return localStorage.getItem("kf-theme") ?? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  } catch {
    return "dark";
  }
}

export default function App() {
  const [route, setRoute] = useState<Route>(readRoute);
  const [theme, setTheme] = useState(initialTheme);
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", getComputedStyle(document.documentElement).getPropertyValue("--bg").trim());
  }, [theme]);
  // 직접 고른 테마만 저장한다. 처음부터 저장하면 OS 설정을 바꿔도 따라가지 않는다.
  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    try { localStorage.setItem("kf-theme", next); } catch { /* 저장 못 해도 동작에는 문제없다 */ }
  };

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

  const nav = (v: View, label: string) => (
    <button className={route.view === v ? "on" : ""} aria-current={route.view === v ? "page" : undefined} onClick={() => go({ view: v })}>{label}</button>
  );

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
          {nav("home", "분석")}
          {nav("history", "판단 기록")}
          {nav("method", "검증 방법")}
          <button onClick={toggleTheme} aria-label={theme === "dark" ? "밝은 테마로" : "어두운 테마로"} className="icon">
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
        {route.view === "method" && <Method />}
        {route.view === "symbol" && (
          <SymbolPage key={route.run ?? route.symbol} route={route} me={me} theme={theme} onLogin={refreshMe}
                      onTab={(tab) => go({ ...route, tab }, true)}
                      onRunSaved={(id) => go({ view: "symbol", run: id }, true)} onOpen={(id) => go({ view: "symbol", run: id })} />
        )}
      </main>
      <footer className="site-footer">
        <p>공개 데이터(Yahoo Finance, Google News)와 통계 모델로 만든 참고 자료이며 투자 권유가 아닙니다. 과거 성과와 시뮬레이션은 미래 수익을 보장하지 않습니다.</p>
      </footer>
    </>
  );
}

const TAB_KEYS = ["summary", "chart", "strategies", "simulation", "valuation", "fundamentals", "personas", "news", "history"];

function SymbolPage({ route, me, theme, onLogin, onTab, onRunSaved, onOpen }: {
  route: Route; me: Me | null; theme: string; onLogin: () => void; onTab: (t: string) => void;
  onRunSaved: (id: string) => void; onOpen: (id: string) => void;
}) {
  const [quote, setQuote] = useState<Quote | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [steps, setSteps] = useState<StepState[] | null>(null);
  const [queued, setQueued] = useState(false);
  const [running, setRunning] = useState(false);
  const [horizon, setHorizon] = useState<Horizon>("mid");
  const [strategy, setStrategy] = useState("");
  const [past, setPast] = useState<RunsResponse | null>(null);
  const [copied, setCopied] = useState(false);
  const symbol = result?.symbol ?? route.symbol ?? quote?.symbol;
  const tab = route.tab && TAB_KEYS.includes(route.tab) ? route.tab : "summary";

  useEffect(() => {
    setError(null);
    if (route.run) {
      api.run(route.run).then((r) => { setResult(r); setHorizon(r.horizon); }).catch((e) => setError(`기록을 불러오지 못했습니다: ${(e as Error).message}`));
    } else if (route.symbol) {
      api.quote(route.symbol).then((q) => {
        setQuote(q);
        rememberSymbol({ symbol: q.symbol, name: q.name, type: q.type, exchange: q.exchange ?? "" });
      }).catch((e) => setError((e as Error).message));
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

  const focusRun = () => {
    onTab("summary");
    setTimeout(() => {
      const el = document.getElementById("run-panel");
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      (document.getElementById("run-btn") ?? document.getElementById("pw"))?.focus({ preventScroll: true });
    }, 50);
  };

  const run = (h: Horizon, pos: { qty: number; avg: number } | null) => {
    if (!symbol) return;
    setRunning(true);
    setError(null);
    setSteps(null);
    onTab("summary");
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
        setTimeout(() => document.getElementById("verdict-title")?.focus(), 100);
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

  const noFin = result ? !result.fundamentals.valuation.available && !(result.fundamentals.valuation.football_field?.length) : false;
  const need = "검증·시뮬레이션을 실행하면 열립니다";
  const passed = result?.validation.strategies.filter((s) => s.weight > 0).length;
  const tabs: TabDef[] = [
    { key: "summary", label: "요약" },
    { key: "chart", label: "차트" },
    { key: "strategies", label: "전략 검증", disabled: !result, hint: need, badge: result ? `통과 ${passed}` : undefined },
    { key: "simulation", label: "시뮬레이션", disabled: !result, hint: need },
    { key: "valuation", label: "가치평가", disabled: !result || noFin, hint: result ? "재무 데이터가 없는 종목입니다 (ETF·코인)" : need },
    { key: "fundamentals", label: "재무·애널리스트", disabled: !result || noFin, hint: result ? "재무 데이터가 없는 종목입니다 (ETF·코인)" : need },
    { key: "personas", label: "투자 대가", disabled: !result, hint: need },
    { key: "news", label: "뉴스", disabled: !result, hint: need, badge: result ? result.news.items.length : undefined },
    { key: "history", label: "판단 기록", badge: past?.runs.length || undefined },
  ];
  const active = tabs.find((t) => t.key === tab && !t.disabled) ? tab : "summary";
  const withSide = active === "summary" || active === "chart";

  const runPanel = (quote || result) && symbol && (
    <RunPanel key={result?.run_id ?? "new"} symbol={symbol} authed={!!me?.authenticated} loginEnabled={!!me?.login_enabled}
              running={running} onRun={run} onLogin={onLogin} horizonHint={result?.horizon} />
  );

  const priceChart = (h: number) => result
    ? <PriceChart candles={result.chart.candles} sma50={result.chart.sma50} sma200={result.chart.sma200} markers={sel?.markers} levels={levels} theme={theme} height={h} />
    : quote ? <PriceChart candles={quote.candles} sma50={quote.sma50} sma200={quote.sma200} theme={theme} height={h} /> : <div className="skeleton chart" style={{ height: h }} />;

  const loadingRun = route.run && !result && !error;

  return (
    <div className="symbol-page">
      {error && (
        <div className="banner error" role="alert">
          <span>{error}</span>
          {!running && symbol && me?.authenticated && <button className="btn ghost" onClick={focusRun}>다시 실행</button>}
        </div>
      )}
      {head ? (
        <header className="sym-head">
          <div className="sym-title">
            <h1>{head.name} <span className="ticker">{head.symbol}</span></h1>
            <p className="sym-meta"><MetaList items={[head.exchange, head.sector, head.industry, `시가총액 ${big(head.cap)}`, `기준일 ${head.asOf}`]} /></p>
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
          {result && (
            <div className="head-verdict">
              <span className={`vbadge v-${result.verdicts[horizon].verdict}`}>{result.verdicts[horizon].label}</span>
              <small>{result.created_at ? dateTime(result.created_at) : "방금"} 판단</small>
            </div>
          )}
          <div className="head-actions">
            <button className="btn primary" onClick={focusRun} disabled={running}>{running ? "실행 중…" : result ? "다시 실행" : "검증·시뮬레이션 실행"}</button>
            {result && (
              <button className="btn outline" onClick={() => { navigator.clipboard?.writeText(location.href); setCopied(true); setTimeout(() => setCopied(false), 2000); }}>
                {copied ? "주소 복사됨" : "결과 공유"}
              </button>
            )}
          </div>
        </header>
      ) : !error && <div className="skeleton head" aria-busy="true" />}

      <TabBar tabs={tabs} active={active} onChange={onTab} label="종목 분석 메뉴" />

      <div className={withSide ? "layout" : "layout full"} role="tabpanel" id={`panel-${active}`} aria-labelledby={`tab-${active}`}>
        <div className="main-col">
          {active === "summary" && (
            loadingRun ? <div className="skeleton block" aria-busy="true" />
              : running && steps ? <Progress steps={steps} queued={queued} />
              : result ? (
                <div className="stack">
                  <VerdictHero r={result} horizon={horizon} onHorizon={setHorizon} />
                  <PlanSummary r={result} horizon={horizon} onOpen={() => onTab("simulation")} />
                  <FactorGrid r={result} horizon={horizon} onOpen={onTab} />
                  <Reasons r={result} />
                  <p className="disclaimer">{result.meta.disclaimer} 데이터 기준 {date(result.as_of)}.</p>
                </div>
              ) : (
                <div className="stack">
                  <section className="card chart-card" aria-label="가격 차트">{priceChart(320)}</section>
                  <section className="card empty">
                    <h2>아직 판단이 없습니다</h2>
                    <p>오른쪽(모바일은 아래) 실행 패널에서 기간을 고르고 실행하면 약 30~60초 뒤 매수·관망·매도 판단과 근거가 여기에 나옵니다.</p>
                    {past && past.runs.length > 0 && <p className="hint">이 종목의 이전 판단이 {past.runs.length}건 있습니다. '판단 기록' 탭에서 볼 수 있습니다.</p>}
                  </section>
                </div>
              )
          )}
          {active === "chart" && (
            <div className="stack">
              <section className="card chart-card" aria-label="가격 차트">
                <div className="chart-legend">
                  <span className="lg sma50">SMA50</span><span className="lg sma200">SMA200</span>
                  {result && <span className="lg lv">손절·목표 ({horizon === "short" ? "단기" : horizon === "mid" ? "중기" : "장기"})</span>}
                  {result && (
                    <label className="marker-pick">매매 화살표
                      <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
                        <option value="">표시 안 함</option>
                        {result.validation.strategies.map((s) => <option key={s.key} value={s.key}>{s.label} ({s.grade})</option>)}
                      </select>
                    </label>
                  )}
                </div>
                {priceChart(480)}
                {result?.quote.partial_bar && <p className="hint">장중 데이터: 마지막 봉은 아직 진행 중이라 검증·백테스트에서 뺐습니다.</p>}
              </section>
              {result && <TrendCard r={result} />}
            </div>
          )}
          {active === "strategies" && result && <Strategies r={result} selected={strategy} onSelect={setStrategy} theme={theme} />}
          {active === "simulation" && result && (
            <div className="stack">
              <PlanCard r={result} horizon={horizon} />
              <div className="two-col"><Analogs r={result} horizon={horizon} /><RiskCard r={result} /></div>
            </div>
          )}
          {active === "valuation" && result && <Valuation r={result} />}
          {active === "fundamentals" && result && <Fundamentals r={result} />}
          {active === "personas" && result && <Personas r={result} />}
          {active === "news" && result && <News r={result} />}
          {active === "history" && (
            <section className="card">
              <h2>{symbol} 판단 기록</h2>
              {past ? <RunsTable data={past} onOpen={onOpen} /> : <p className="hint">불러오는 중…</p>}
            </section>
          )}
        </div>
        {withSide && (
          <aside className="side">
            {runPanel}
            {result && (
              <section className="card meta">
                <h2>이 결과</h2>
                <p className="hint"><MetaList items={[
                  result.created_at ? `${dateTime(result.created_at)} 실행` : "방금 실행",
                  `${((result.meta.duration_ms ?? 0) / 1000).toFixed(1)}초`,
                  result.position ? `보유 ${result.position.qty}주, 평균 ${usd(result.position.avg_price)}` : null,
                ]} /></p>
                <p className="hint">해설 모델 {result.meta.llm_model ?? "없음"} ({Math.round(result.meta.llm_calls ?? 0)}회 호출)</p>
              </section>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}
