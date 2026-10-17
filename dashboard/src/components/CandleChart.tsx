/**
 * CandleChart: lightweight-charts 로 캔들스틱 차트를 그리는 컴포넌트
 * ============================================================
 * [이 파일이 하는 일]
 *   부모(App)에게서 받은 캔들 배열(candles)을 TradingView의
 *   lightweight-charts 형식으로 변환해 캔들스틱 차트로 그린다.
 *
 * [핵심 개념]
 *   - 차트 "인스턴스"는 컴포넌트가 처음 마운트될 때 딱 한 번 만든다 (useEffect []).
 *   - 데이터가 바뀔 때마다(candles prop 변경) series.setData 로 갱신만 한다.
 *     -> 매번 차트를 새로 만들지 않아 깜빡임이 없고 부드럽다.
 *   - lightweight-charts의 시간(time)은 "초 단위 UTC epoch"(UTCTimestamp)이다.
 *     우리 데이터의 window_start(ISO 문자열)를 초 단위로 변환해줘야 한다.
 *   - 백테스트 매수/매도는 series.setMarkers 로 캔들 위에 화살표로 표시한다.
 *   - 뉴스는 같은 setMarkers 에 봉마다 감정 색 점(●)으로 얹는다. 점을 누르면(봉을 누르면) 부모가 그 봉의
 *     기사 목록(제목·링크)을 보여 준다. 기사의 시각은 collected_at 이 속한 봉이다 (백테스트 기준 시각과 같다).
 */
import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type LogicalRange,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { BacktestMarker, Candle, NewsItem } from "../api";

interface Props {
  candles: Candle[];
  // 백테스트 매수/매도 이벤트 (없으면 마커를 그리지 않는다)
  markers?: BacktestMarker[];
  // 뉴스 신호 (없으면 점을 그리지 않는다) + 봉 길이(ms): 기사를 어느 봉에 찍을지 계산하는 데 쓴다.
  news?: NewsItem[];
  stepMs?: number | null;
  // 봉을 눌렀을 때 (time = 그 봉의 시작, UTC epoch 초)
  onBarClick?: (time: number) => void;
  // 보이는 시간 구간이 바뀔 때 (보조 차트가 시간축을 맞추는 데 쓴다)
  onVisibleTimeRange?: (range: { from: number; to: number } | null) => void;
  // 심볼/간격 등 "다른 차트가 됐다"를 알리는 키. 바뀌면 시간축을 처음부터 다시 맞춘다.
  viewKey: string;
  // 사용자가 왼쪽 끝 근처까지 스크롤했을 때 호출 (과거 데이터를 더 불러오라는 신호)
  onNeedOlder?: () => void;
}

// 보이는 구간의 왼쪽 끝이 데이터 시작에서 이 개수 이내로 가까워지면 과거를 더 불러온다.
const LOAD_EDGE_BARS = 30;

const toSec = (c: Candle) => (Date.parse(c.window_start) / 1000) as UTCTimestamp;

// 리스크 청산 사유 -> 마커에 붙일 짧은 꼬리표 (신호 청산은 표시 생략)
const REASON_TAG: Record<string, string> = {
  stop_loss: "SL",
  take_profit: "TP",
  trailing_stop: "TS",
};

/** 백테스트 이벤트 -> lightweight-charts 마커. 매수는 봉 아래 ▲(초록), 매도는 봉 위 ▼(빨강). */
function toSeriesMarkers(markers: BacktestMarker[], candleTimes: Set<number>): SeriesMarker<Time>[] {
  return markers
    .filter((m) => candleTimes.has(m.time)) // 화면에 없는 봉의 마커는 제외
    .sort((a, b) => a.time - b.time) // lightweight-charts 는 시간 오름차순을 요구
    .map((m): SeriesMarker<Time> => {
      if (m.side === "buy") {
        // 매수는 화살표만 (촘촘한 구간에서 글자가 겹치지 않게)
        return {
          time: m.time as UTCTimestamp,
          position: "belowBar",
          shape: "arrowUp",
          color: "#26a69a",
        };
      }
      const pct = m.pnl_pct === null ? "" : `${m.pnl_pct >= 0 ? "+" : ""}${(m.pnl_pct * 100).toFixed(2)}%`;
      const tag = REASON_TAG[m.reason];
      return {
        time: m.time as UTCTimestamp,
        position: "aboveBar",
        shape: "arrowDown",
        color: "#ef5350",
        text: tag ? `${tag} ${pct}` : pct, // 매도는 손익 % (리스크 청산이면 SL/TP/TS 꼬리표)
      };
    });
}

// 뉴스 점 색 (감정). 중립은 회색.
const NEWS_COLOR = { positive: "#26a69a", neutral: "#8b95a5", negative: "#ef5350" } as const;

/** 기사들을 봉(시작 시각)별로 묶는다. collected_at 이 속한 봉 = floor(시각 / 봉 길이) * 봉 길이 (date_bin epoch 기준과 같다). */
export function groupNewsByBar(news: NewsItem[], stepMs: number | null): Map<number, NewsItem[]> {
  const out = new Map<number, NewsItem[]>();
  if (!stepMs) return out;
  const stepSec = stepMs / 1000;
  for (const n of news) {
    const sec = Date.parse(n.collected_at) / 1000;
    if (!Number.isFinite(sec)) continue;
    const bar = Math.floor(sec / stepSec) * stepSec;
    const list = out.get(bar);
    if (list) list.push(n);
    else out.set(bar, [n]);
  }
  return out;
}

/** 봉 안 기사들의 대표 감정: 확률 순합(긍정-부정)이 +-0.15 를 넘으면 긍정/부정, 아니면 중립. */
export function barSentiment(items: NewsItem[]): keyof typeof NEWS_COLOR {
  const net = items.reduce((a, n) => a + ((n.p_pos ?? 0) - (n.p_neg ?? 0)), 0) / Math.max(items.length, 1);
  return net > 0.15 ? "positive" : net < -0.15 ? "negative" : "neutral";
}

/** 뉴스 -> 봉마다 점 1개 (기사가 여러 건이면 개수 표시). 화면에 없는 봉은 제외. */
function toNewsMarkers(news: NewsItem[], stepMs: number | null, candleTimes: Set<number>): SeriesMarker<Time>[] {
  const out: SeriesMarker<Time>[] = [];
  for (const [bar, items] of groupNewsByBar(news, stepMs)) {
    if (!candleTimes.has(bar)) continue;
    out.push({
      time: bar as UTCTimestamp,
      position: "aboveBar", // 캔들 위에 얹어 몸통에 가리지 않게 한다 (백테스트 매도 화살표와는 위아래로 쌓인다)
      shape: "circle",
      size: 2,
      color: NEWS_COLOR[barSentiment(items)],
      text: items.length > 1 ? String(items.length) : undefined,
    });
  }
  return out;
}

export function CandleChart({ candles, markers, news, stepMs, onBarClick, onVisibleTimeRange, viewKey, onNeedOlder }: Props) {
  // 차트를 그릴 DOM 요소(div)에 대한 참조
  const containerRef = useRef<HTMLDivElement>(null);
  // 차트/시리즈 인스턴스를 리렌더 사이에 유지하기 위한 참조
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  // 거래량 막대(히스토그램) 시리즈
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  // 직전에 그린 캔들 배열과 viewKey: 이번 변경이 "처음 그리기 / 과거 앞붙임 / 최신 갱신" 중 무엇인지 가르는 데 쓴다.
  const prevRef = useRef<Candle[]>([]);
  const viewKeyRef = useRef<string>(viewKey);
  // 다른 차트로 바뀐 뒤 "새 차트의 첫 데이터"를 아직 못 그렸는지. true 인 동안 들어오는 데이터는 '처음 그리기'로 처리한다.
  const needInitialRef = useRef(true);
  // 사용자가 직접 조작(드래그/휠/터치)하기 전에는 '왼쪽 끝 감지'를 끈다.
  //  (처음 fitContent 직후에는 보이는 구간이 항상 데이터 시작에 닿아 있어서, 끄지 않으면 과거를 계속 자동으로 끌어온다)
  const armedRef = useRef(false);
  // 콜백은 렌더마다 새로 만들어지므로 ref 로 최신 것만 참조한다 (차트 구독은 마운트 때 1번만 건다).
  const onNeedOlderRef = useRef(onNeedOlder);
  onNeedOlderRef.current = onNeedOlder;
  const onBarClickRef = useRef(onBarClick);
  onBarClickRef.current = onBarClick;
  const onRangeRef = useRef(onVisibleTimeRange);
  onRangeRef.current = onVisibleTimeRange;

  // 1) 마운트 시 1회: 차트 인스턴스 생성 + 리사이즈 대응
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: "#0e1117" },
        textColor: "#d1d4dc",
      },
      grid: {
        vertLines: { color: "#1e222d" },
        horzLines: { color: "#1e222d" },
      },
      timeScale: {
        timeVisible: true, // 분 단위라 시:분까지 보이게
        secondsVisible: false,
        borderColor: "#2a2e39",
      },
      // 가격축 폭을 보조 차트(news_score)와 같게 고정해 두 차트의 시간축이 세로로 정렬되게 한다.
      rightPriceScale: { borderColor: "#2a2e39", minimumWidth: 72 },
      height: 480,
      autoSize: true, // 컨테이너 크기에 맞춰 자동 조정
    });

    // lightweight-charts v4: addCandlestickSeries 로 캔들 시리즈 추가
    const series = chart.addCandlestickSeries({
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderVisible: false,
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
    });

    // 거래량 히스토그램: 별도 priceScaleId("volume")를 써서 캔들과 축을 분리한다.
    const volume = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "volume", // 캔들(right)과 다른 독립 스케일
    });
    // 거래량을 차트 하단 ~25% 영역에만 그리도록 여백(margin) 설정.
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.75, bottom: 0 },
    });

    chartRef.current = chart;
    seriesRef.current = series;
    volumeRef.current = volume;

    // 이 차트 인스턴스는 비어 있다. "이미 그렸다"는 기억(prev/needInitial/armed)은 이전 인스턴스의 것이므로 지운다.
    //  (React StrictMode 는 개발 중 마운트 effect 를 정리했다가 다시 실행해 차트를 새로 만든다. 이 초기화가 없으면
    //   데이터 effect 가 '최신 갱신(update)' 경로로 가서 빈 시리즈에 봉을 1개만 넣고, 나머지 119개는 영영 안 그려진다.)
    prevRef.current = [];
    needInitialRef.current = true;
    armedRef.current = false;

    // 창 크기가 바뀌면 차트 폭도 맞춘다
    const handleResize = () => chart.applyOptions({ width: container.clientWidth });
    window.addEventListener("resize", handleResize);

    // 왼쪽 끝 근처까지 스크롤/확대 축소하면 과거 데이터를 더 불러오라고 알린다.
    const arm = () => {
      armedRef.current = true;
    };
    container.addEventListener("pointerdown", arm);
    container.addEventListener("wheel", arm, { passive: true });
    const handleRange = (range: LogicalRange | null) => {
      if (range && armedRef.current && range.from < LOAD_EDGE_BARS) onNeedOlderRef.current?.();
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(handleRange);

    // 봉 클릭 -> 부모가 그 봉의 뉴스를 보여 준다. 보이는 시간 구간 변경 -> 보조 차트가 따라온다.
    const handleClick = (param: { time?: Time }) => {
      if (typeof param.time === "number") onBarClickRef.current?.(param.time);
    };
    chart.subscribeClick(handleClick);
    const handleTimeRange = (range: { from: Time; to: Time } | null) => {
      if (!range || typeof range.from !== "number" || typeof range.to !== "number") return onRangeRef.current?.(null);
      onRangeRef.current?.({ from: range.from, to: range.to });
    };
    chart.timeScale().subscribeVisibleTimeRangeChange(handleTimeRange);

    // 언마운트 시 정리 (메모리 누수 방지)
    return () => {
      chart.unsubscribeClick(handleClick);
      chart.timeScale().unsubscribeVisibleTimeRangeChange(handleTimeRange);
      window.removeEventListener("resize", handleResize);
      container.removeEventListener("pointerdown", arm);
      container.removeEventListener("wheel", arm);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(handleRange);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      volumeRef.current = null;
    };
  }, []);

  // 2) candles 가 바뀔 때마다: 변경 종류에 맞게 캔들 + 거래량을 갱신한다.
  //    - 처음 그리기(차트가 바뀌었거나 비어 있었음): 전체를 넣고 시간축을 데이터에 맞춘다.
  //    - 과거 앞붙임(왼쪽 스크롤): 전체를 다시 넣되, 보던 구간이 튀지 않게 늘어난 개수만큼 밀어 준다.
  //    - 최신 갱신(5초 폴링): update() 로 마지막 봉만 갱신/추가한다. 사용자의 확대·이동은 그대로 유지된다.
  useEffect(() => {
    const series = seriesRef.current;
    const volume = volumeRef.current;
    const chart = chartRef.current;
    if (!series || !volume || !chart) return;

    // 심볼/간격이 바뀌면 viewKey 는 즉시 바뀌지만, candles 는 부모가 비우고 새로 받아 올 때까지 한 렌더 동안
    // 이전 차트의 봉 그대로다. 그 렌더에서 그려 버리면 이전 데이터에 시간축을 맞춰 놓고, 뒤이어 오는
    // 진짜 데이터는 '최신 갱신'으로 오인돼 화면이 데이터 밖(빈 공간)에 머문다.
    // 그래서 키가 바뀌면 일단 비우고, 새 데이터가 들어오는 첫 렌더를 '처음 그리기'로 취급한다.
    if (viewKeyRef.current !== viewKey) {
      viewKeyRef.current = viewKey;
      needInitialRef.current = true;
      prevRef.current = [];
      armedRef.current = false;
      series.setData([]);
      volume.setData([]);
      return; // 이 렌더의 candles 는 이전 차트의 것이므로 그리지 않는다
    }
    const prev = prevRef.current;

    // 거래량 막대: 상승봉(close>=open)은 초록, 하락봉은 빨강 (반투명)
    const barOf = (c: Candle) => ({ time: toSec(c), open: c.open, high: c.high, low: c.low, close: c.close });
    const volOf = (c: Candle) => ({
      time: toSec(c),
      value: c.volume,
      color: c.close >= c.open ? "rgba(38,166,154,0.5)" : "rgba(239,83,80,0.5)",
    });
    const setAll = () => {
      series.setData(candles.map(barOf));
      volume.setData(candles.map(volOf));
    };

    if (candles.length === 0) {
      series.setData([]);
      volume.setData([]);
      armedRef.current = false;
      needInitialRef.current = true;
    } else if (needInitialRef.current || prev.length === 0) {
      setAll();
      chart.timeScale().fitContent(); // 새 차트: 불러온 데이터가 한눈에 보이게
      armedRef.current = false;
      needInitialRef.current = false;
    } else if (toSec(candles[0]) < toSec(prev[0])) {
      // 과거가 앞에 붙었다: 늘어난 봉 개수만큼 보이는 구간을 밀어 같은 봉이 같은 자리에 보이게 한다.
      const added = candles.findIndex((c) => toSec(c) >= toSec(prev[0]));
      const range = chart.timeScale().getVisibleLogicalRange();
      setAll();
      if (range && added > 0) {
        chart.timeScale().setVisibleLogicalRange({ from: range.from + added, to: range.to + added });
      }
    } else if (toSec(candles[0]) > toSec(prev[0])) {
      // 앞쪽이 잘려 나갔다(공백 후 새로 시작 등): 처음부터 다시 맞춘다.
      setAll();
      chart.timeScale().fitContent();
    } else {
      // 최신 구간 갱신: 직전 마지막 봉 이후만 update() (같은 시각이면 교체, 새 시각이면 추가)
      const lastSec = toSec(prev[prev.length - 1]);
      for (const c of candles) {
        if (toSec(c) >= lastSec) {
          series.update(barOf(c));
          volume.update(volOf(c));
        }
      }
    }
    prevRef.current = candles;
  }, [candles, viewKey]);

  // 3) 마커 갱신: 캔들 데이터가 먼저 들어간 뒤(위 effect) 백테스트 + 뉴스 마커를 한 번에 얹는다.
  //    둘 다 없으면 빈 배열로 지워서, 백테스트/뉴스를 끄면 마커도 사라진다. (lightweight-charts 는 시간 오름차순 필수)
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    const times = new Set(candles.map((c) => Date.parse(c.window_start) / 1000));
    const all = [
      ...(markers && markers.length > 0 ? toSeriesMarkers(markers, times) : []),
      ...(news && news.length > 0 ? toNewsMarkers(news, stepMs ?? null, times) : []),
    ].sort((a, b) => (a.time as number) - (b.time as number));
    series.setMarkers(all);
  }, [candles, markers, news, stepMs]);

  return <div ref={containerRef} style={{ width: "100%" }} />;
}
