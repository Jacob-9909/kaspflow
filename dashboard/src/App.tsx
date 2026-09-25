/**
 * App: 대시보드 최상위 컴포넌트
 * ============================================================
 * [이 파일이 하는 일]
 *   1. 마운트 시 심볼 목록을 불러오고, 첫 심볼을 자동 선택한다.
 *   2. 선택된 심볼의 1분봉을 5초마다 다시 불러온다(폴링) -> 실시간처럼 갱신.
 *   3. 최신 캔들로 요약 지표(시/고/저/종/거래량)를 카드로 보여준다.
 *   4. 캔들 데이터를 CandleChart 에 넘겨 차트를 그린다.
 *
 * [읽는 순서 추천]
 *   App() 안의 useEffect 2개 -> return JSX (헤더 / 심볼선택 / 지표카드 / 차트)
 */
import { useEffect, useState } from "react";
import { fetchSymbols, fetchOhlc, fetchIntervals, type Candle } from "./api";
import { CandleChart } from "./components/CandleChart";
import { SymbolSelector } from "./components/SymbolSelector";

// 폴링 주기(ms). 백엔드/DB 갱신 주기(Spark 5초 트리거)와 맞췄다.
const REFRESH_MS = 5000;

// 숫자를 보기 좋게(천단위 콤마) 표시
const fmt = (n: number, digits = 2) =>
  n.toLocaleString("en-US", { maximumFractionDigits: digits });

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span className="metric__label">{label}</span>
      <span className="metric__value">{value}</span>
    </div>
  );
}

export default function App() {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [intervals, setIntervals] = useState<string[]>(["1m"]);
  const [interval, setInterval_] = useState<string>("1m");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [error, setError] = useState<string | null>(null);

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
  }, []);

  // 2) 선택 심볼/인터벌이 바뀌거나, 5초마다: 봉 다시 로드
  useEffect(() => {
    if (!selected) return;

    let cancelled = false; // 언마운트 후 setState 방지 플래그

    const load = () => {
      fetchOhlc(selected, interval)
        .then((data) => {
          if (!cancelled) {
            setCandles(data);
            setError(null);
          }
        })
        .catch((e) => {
          if (!cancelled) setError(String(e));
        });
    };

    load(); // 즉시 1회
    // window.setInterval: 위 상태변수 interval 과 이름이 겹쳐 window. 으로 명시
    const timer = window.setInterval(load, REFRESH_MS);

    // 심볼/인터벌이 바뀌거나 언마운트되면 타이머 정리
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [selected, interval]);

  // 최신 캔들(요약 지표용)
  const latest = candles.length > 0 ? candles[candles.length - 1] : null;

  return (
    <div className="app">
      <header className="header">
        <h1>📈 실시간 암호화폐 시세 대시보드</h1>
        <p className="muted">
          Binance → Kafka → Spark Structured Streaming → PostgreSQL → FastAPI → React
        </p>
      </header>

      <SymbolSelector symbols={symbols} selected={selected} onSelect={setSelected} />

      {/* 인터벌(봉 간격) 선택 — 심볼 선택과 같은 칩 스타일 */}
      <div className="symbol-selector">
        {intervals.map((iv) => (
          <button
            key={iv}
            className={iv === interval ? "chip chip--active" : "chip"}
            onClick={() => setInterval_(iv)}
          >
            {iv}
          </button>
        ))}
      </div>

      {error && <p className="error">데이터를 불러오지 못했습니다: {error}</p>}

      {latest ? (
        <>
          <div className="metrics">
            <MetricCard label="종가 (close)" value={fmt(latest.close)} />
            <MetricCard label="고가 (high)" value={fmt(latest.high)} />
            <MetricCard label="저가 (low)" value={fmt(latest.low)} />
            <MetricCard label="거래량 (volume)" value={fmt(latest.volume, 4)} />
            <MetricCard label="체결 수 (trades)" value={fmt(latest.trade_count, 0)} />
          </div>
          <div className="chart-card">
            <CandleChart candles={candles} />
          </div>
        </>
      ) : (
        !error && (
          <p className="muted">
            선택한 심볼의 집계 데이터가 아직 없습니다. producer/spark 가 데이터를
            쌓을 때까지 1~2분 기다린 뒤 자동으로 표시됩니다.
          </p>
        )
      )}
    </div>
  );
}
