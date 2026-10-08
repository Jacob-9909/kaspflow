/**
 * App: 대시보드 최상위 컴포넌트
 * ============================================================
 * [이 파일이 하는 일]
 *   1. 마운트 시 심볼 목록을 불러오고, 첫 심볼을 자동 선택한다.
 *   2. 선택된 심볼의 1분봉을 5초마다 다시 불러온다(폴링) -> 실시간처럼 갱신.
 *   3. 최신 캔들 + 첫 캔들로 등락(가격/퍼센트)을 계산해 가격 헤더로 강조한다.
 *   4. 요약 지표(고/저/거래량/체결수)를 색상 코딩된 카드로 보여준다.
 *   5. 캔들 데이터를 CandleChart 에 넘겨 차트를 그린다.
 *      봉은 한 배열로 유지한다: 5초 폴링은 최신 구간만 뒤쪽에 덮어쓰고(mergeTail),
 *      차트를 왼쪽 끝까지 스크롤하면 과거 구간을 앞에 이어 붙인다(loadOlder -> prependOlder).
 *   6. 백테스트를 켜면 /backtest 결과(매수/매도 마커, 성과 지표)를 함께 불러와
 *      차트에 마커를 찍고 BacktestPanel 에 결과를 보여준다.
 *
 * [읽는 순서 추천]
 *   App() 안의 useEffect 2개 -> 파생값(latest/first/change) -> return JSX
 *   (헤더+라이브 / 툴바 / 가격헤더 / 지표카드 / 차트)
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchSymbols,
  fetchOhlcPage,
  fetchIntervals,
  fetchBacktest,
  fetchBacktestStrategies,
  type BacktestConfig,
  type BacktestResult,
  type Candle,
  type StrategyInfo,
} from "./api";
import { mergeTail, prependOlder, stepMs } from "./candles";
import { BacktestPanel } from "./components/BacktestPanel";
import { CandleChart } from "./components/CandleChart";
import { SymbolSelector } from "./components/SymbolSelector";

// 폴링 주기(ms). 백엔드/DB 갱신 주기(Spark 5초 트리거)와 맞췄다.
const REFRESH_MS = 5000;

// 백테스트를 꺼 둔 평소에 차트가 처음 불러오는 봉 수. 켜면 백테스트의 '기간'과 같은 수를 불러온다.
const DEFAULT_LIMIT = 120;

// 5초 폴링 때 받는 최신 구간 크기. 이미 가진 봉의 뒤쪽을 이 구간으로 덮어쓴다.
const TAIL_LIMIT = 120;

// 왼쪽으로 스크롤할 때 한 번에 더 불러오는 과거 봉 수 (백엔드 /ohlc 상한 1000 이내).
const OLDER_CHUNK = 1000;

// 메모리/렌더 보호용: 이만큼 불러오면 더는 과거를 이어 붙이지 않는다.
const MAX_LOADED = 20000;

// 숫자를 보기 좋게(천단위 콤마) 표시
const fmt = (n: number, digits = 2) =>
  n.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });

// "몇 초 전" 형태의 상대 시각
function ago(ms: number): string {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 2) return "방금";
  if (s < 60) return `${s}초 전`;
  const m = Math.floor(s / 60);
  return `${m}분 ${s % 60}초 전`;
}

function MetricCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
}) {
  const cls = tone ? `metric metric--${tone}` : "metric";
  return (
    <div className={cls}>
      <span className="metric__label">{label}</span>
      <span className={`metric__value${tone ? ` ${tone}` : ""}`}>{value}</span>
    </div>
  );
}

export default function App() {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [intervals, setIntervals] = useState<string[]>(["1m"]);
  const [interval, setInterval_] = useState<string>("1m");
  const [candles, setCandles] = useState<Candle[]>([]);
  // 과거 이어 붙이기 상태: 저장소의 가장 오래된 버킷, 불러오는 중인지, 더는 없는지
  const [earliest, setEarliest] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [noMoreOlder, setNoMoreOlder] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  // 백테스트: 전략 목록(서버 제공), 켜짐 여부, 적용된 설정, 결과
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [btOn, setBtOn] = useState(false);
  const [btConfig, setBtConfig] = useState<BacktestConfig | null>(null);
  const [btResult, setBtResult] = useState<BacktestResult | null>(null);
  const [btError, setBtError] = useState<string | null>(null);
  const [btLoading, setBtLoading] = useState(false);
  // 상대시각("N초 전")을 1초마다 다시 그리기 위한 틱
  const [, setTick] = useState(0);

  // 1) 마운트 시 1회: 심볼 목록 + 인터벌 목록 로드 + 첫 심볼 자동 선택
  useEffect(() => {
    fetchSymbols()
      .then((list) => {
        setSymbols(list);
        if (list.length > 0) setSelected((prev) => prev ?? list[0]);
      })
      .catch((e) => setError(String(e)));
    fetchIntervals()
      .then((list) => {
        if (list.length > 0) setIntervals(list);
      })
      .catch(() => {
        /* 실패해도 기본 ["1m"] 유지 */
      });
    // 백엔드가 백테스트를 지원하면 전략 목록으로 기본 설정을 만든다 (미지원이면 패널을 숨김)
    fetchBacktestStrategies().then((res) => {
      if (!res || res.strategies.length === 0) return;
      const first = res.strategies[0];
      setStrategies(res.strategies);
      setBtConfig({
        strategy: first.id,
        limit: 500,
        initialCapital: 10000,
        feeBps: res.risk_defaults.fee_bps,
        stopLossPct: null,
        takeProfitPct: null,
        trailingStopPct: null,
        params: first.params,
      });
    });
  }, []);

  // 차트가 처음 불러올 봉 수. 백테스트를 켜면 마커가 전부 보이도록 백테스트 '기간'과 같게 한다.
  const initialLimit = btOn && btConfig ? btConfig.limit : DEFAULT_LIMIT;
  // "다른 차트인가"를 가르는 키. 바뀌면 불러 둔 과거를 버리고 처음부터 다시 시작한다.
  const viewKey = `${selected}|${interval}|${initialLimit}`;
  const viewKeyRef = useRef(viewKey);
  viewKeyRef.current = viewKey;

  // 비동기 콜백(과거 로딩)에서 최신 값을 읽기 위한 ref
  const candlesRef = useRef<Candle[]>([]);
  candlesRef.current = candles;
  const earliestRef = useRef<string | null>(null);
  earliestRef.current = earliest;
  const loadingOlderRef = useRef(false);
  const noMoreOlderRef = useRef(false);
  noMoreOlderRef.current = noMoreOlder;

  // 2) 캔들 로드: 차트가 바뀌면 초기 로드, 이후 5초마다 최신 구간(tail)만 받아 뒤쪽에 덮어쓴다.
  //    (이전에는 매번 '최근 N봉 통째 교체'라 과거를 불러와도 5초 뒤 사라졌다)
  useEffect(() => {
    if (!selected) return;

    let cancelled = false; // 언마운트/차트 전환 후 setState 방지 플래그
    const step = stepMs(interval);

    // 새 차트: 이전 차트의 봉과 과거 이어 붙이기 상태를 비운다.
    setCandles([]);
    setEarliest(null);
    setNoMoreOlder(false);
    setLoadingOlder(false);
    loadingOlderRef.current = false;

    const loadInitial = () =>
      fetchOhlcPage(selected, interval, initialLimit)
        .then((page) => {
          if (cancelled) return;
          setCandles(page.candles);
          setEarliest(page.earliest);
          setError(null);
          setUpdatedAt(Date.now());
        })
        .catch((e) => {
          if (!cancelled) setError(String(e));
        });

    const pollTail = () =>
      fetchOhlcPage(selected, interval, TAIL_LIMIT)
        .then((page) => {
          if (cancelled) return;
          // 가진 봉과 이어지지 않으면(탭이 오래 멈췄다 돌아온 경우 등) 이 구간으로 새로 시작한다.
          setCandles((prev) => mergeTail(prev, page.candles, step) ?? page.candles);
          setError(null);
          setUpdatedAt(Date.now());
        })
        .catch((e) => {
          if (!cancelled) setError(String(e));
        });

    loadInitial();
    // window.setInterval: 위 상태변수 interval 과 이름이 겹쳐 window. 으로 명시
    const timer = window.setInterval(pollTail, REFRESH_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [selected, interval, initialLimit]);

  // 과거 이어 붙이기: 차트가 왼쪽 끝 근처까지 스크롤되면 호출된다.
  const loadOlder = useCallback(async () => {
    if (!selected) return;
    const cur = candlesRef.current;
    if (cur.length === 0 || loadingOlderRef.current || noMoreOlderRef.current) return;

    // 가진 가장 오래된 봉이 저장소의 첫 버킷이면 더 없음
    const oldest = cur[0].window_start;
    const earliestKnown = earliestRef.current;
    if (earliestKnown && Date.parse(oldest) <= Date.parse(earliestKnown)) {
      setNoMoreOlder(true);
      return;
    }
    if (cur.length >= MAX_LOADED) {
      setNoMoreOlder(true);
      return;
    }

    const key = viewKeyRef.current;
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    try {
      const page = await fetchOhlcPage(selected, interval, OLDER_CHUNK, oldest);
      if (viewKeyRef.current !== key) return; // 기다리는 사이 심볼/간격이 바뀌었으면 버린다
      // 가진 첫 봉보다 과거인 봉이 하나도 없으면 더는 진전이 없다(끝에 도달했거나, before 를 모르는 구버전 서버).
      // 이때 멈추지 않으면 같은 요청을 왼쪽 가장자리마다 무한히 되풀이한다.
      const oldestMs = Date.parse(oldest);
      if (!page.candles.some((c) => Date.parse(c.window_start) < oldestMs)) {
        setNoMoreOlder(true);
        return;
      }
      setCandles((prev) => prependOlder(prev, page.candles));
      if (page.earliest) setEarliest(page.earliest);
      if (page.earliest && Date.parse(page.candles[0].window_start) <= Date.parse(page.earliest)) {
        setNoMoreOlder(true); // 이번에 저장소의 첫 버킷까지 도달
      }
    } catch (e) {
      if (viewKeyRef.current === key) setError(String(e));
    } finally {
      if (viewKeyRef.current === key) {
        loadingOlderRef.current = false;
        setLoadingOlder(false);
      }
    }
  }, [selected, interval]);

  // 백테스트: 켜져 있으면 5초마다 결과를 다시 계산해 받아 온다. (캔들 로딩과는 독립)
  useEffect(() => {
    if (!selected) return;

    let cancelled = false;
    const bt = btOn ? btConfig : null;

    if (!bt) {
      setBtResult(null);
      setBtError(null);
      return;
    }

    const run = () =>
      fetchBacktest(selected, interval, bt)
        .then((res) => {
          if (!cancelled) {
            setBtResult(res);
            setBtError(null);
          }
        })
        .catch((e) => {
          if (!cancelled) setBtError(e instanceof Error ? e.message : String(e));
        })
        .finally(() => {
          if (!cancelled) setBtLoading(false);
        });

    setBtLoading(true);
    run();
    const timer = window.setInterval(run, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [selected, interval, btOn, btConfig]);

  // 3) 상대시각 라벨을 1초마다 갱신
  useEffect(() => {
    const t = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  // 파생값: 최신/첫 캔들과 등락 계산
  //   등락은 "처음 보이는 구간(initialLimit 봉)" 기준으로 계산한다. 과거를 더 불러와도 헤더 값이 바뀌지 않게 한다.
  const latest = candles.length > 0 ? candles[candles.length - 1] : null;
  const shownBars = candles.slice(-initialLimit);
  const first = shownBars.length > 0 ? shownBars[0] : null;
  const changeAbs = latest && first ? latest.close - first.open : 0;
  const changePct = latest && first && first.open !== 0 ? (changeAbs / first.open) * 100 : 0;
  const up = changeAbs >= 0;
  const tone: "up" | "down" = up ? "up" : "down";

  // 라이브 상태: 마지막 갱신이 15초 이내면 "실시간"
  const isLive = updatedAt !== null && Date.now() - updatedAt < 15000;

  // 다른 심볼/간격의 이전 결과가 잠깐 남아 있어도 마커가 엉뚱한 차트에 찍히지 않게 걸러낸다.
  const btShown =
    btOn && btResult && btResult.symbol === selected && btResult.interval === interval ? btResult : null;

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <div className="brand__logo">📈</div>
          <div>
            <h1 className="brand__title">실시간 암호화폐 시세</h1>
            <p className="brand__subtitle">
              Binance → Kafka → Spark → PostgreSQL → FastAPI → React
            </p>
          </div>
        </div>
        <span className="live">
          <span className={`live__dot${isLive ? "" : " live__dot--stale"}`} />
          {isLive ? "실시간" : "연결 대기"}
          {updatedAt && <span style={{ color: "var(--muted)" }}>· {ago(updatedAt)}</span>}
        </span>
      </header>

      <div className="toolbar">
        <div className="toolbar__group">
          <span className="toolbar__label">심볼</span>
          <SymbolSelector symbols={symbols} selected={selected} onSelect={setSelected} />
        </div>
        <div className="toolbar__group">
          <span className="toolbar__label">간격</span>
          <div className="symbol-selector">
            {intervals.map((iv) => (
              <button
                key={iv}
                className={iv === interval ? "chip chip--sm chip--active" : "chip chip--sm"}
                onClick={() => setInterval_(iv)}
              >
                {iv}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error && <p className="error">⚠️ 데이터를 불러오지 못했습니다: {error}</p>}

      {latest ? (
        <>
          <div className="price-head">
            <div>
              <div className="price-head__symbol">{selected}</div>
              <div className="price-head__price">{fmt(latest.close)}</div>
            </div>
            <div className={`price-head__change ${up ? "pill-up" : "pill-down"}`}>
              {up ? "▲" : "▼"} {fmt(Math.abs(changeAbs))} ({up ? "+" : "-"}
              {fmt(Math.abs(changePct))}%)
            </div>
            <div className="price-head__spacer" />
            <div className="price-head__meta">
              <div>
                <span className="price-head__meta-label">고가</span>
                <span className="price-head__meta-value up">{fmt(latest.high)}</span>
              </div>
              <div>
                <span className="price-head__meta-label">저가</span>
                <span className="price-head__meta-value down">{fmt(latest.low)}</span>
              </div>
              <div>
                <span className="price-head__meta-label">최근 {shownBars.length}봉</span>
                <span className="price-head__meta-value">{interval}</span>
              </div>
            </div>
          </div>

          <div className="metrics">
            <MetricCard label="시가 (open)" value={fmt(latest.open)} />
            <MetricCard label="종가 (close)" value={fmt(latest.close)} tone={tone} />
            <MetricCard label="거래량 (volume)" value={fmt(latest.volume, 4)} />
            <MetricCard label="체결 수 (trades)" value={fmt(latest.trade_count, 0)} />
          </div>

          <div className="chart-card">
            <CandleChart candles={candles} markers={btShown?.markers} viewKey={viewKey} onNeedOlder={loadOlder} />
            <div className="chart-status">
              <span>
                불러온 {candles.length.toLocaleString()}봉
                {candles[0] && ` · ${new Date(candles[0].window_start).toLocaleDateString("ko-KR")} ~`}
              </span>
              <span>
                {loadingOlder
                  ? "과거 불러오는 중…"
                  : noMoreOlder
                    ? "저장된 가장 오래된 데이터입니다"
                    : "← 왼쪽으로 스크롤하면 과거를 더 불러옵니다"}
              </span>
            </div>
          </div>

          {strategies.length > 0 && btConfig && (
            <BacktestPanel
              strategies={strategies}
              interval={interval}
              enabled={btOn}
              onToggle={setBtOn}
              config={btConfig}
              onApply={setBtConfig}
              result={btShown}
              loading={btLoading}
              error={btError}
            />
          )}
        </>
      ) : (
        !error && (
          <div className="empty">
            선택한 심볼의 집계 데이터가 아직 없습니다.
            <br />
            producer/spark 가 데이터를 쌓을 때까지 1~2분 기다리면 자동으로 표시됩니다.
          </div>
        )
      )}
    </div>
  );
}
