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
 */
import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Candle } from "../api";

interface Props {
  candles: Candle[];
}

export function CandleChart({ candles }: Props) {
  // 차트를 그릴 DOM 요소(div)에 대한 참조
  const containerRef = useRef<HTMLDivElement>(null);
  // 차트/시리즈 인스턴스를 리렌더 사이에 유지하기 위한 참조
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  // 거래량 막대(히스토그램) 시리즈
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);

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
      rightPriceScale: { borderColor: "#2a2e39" },
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

    // 창 크기가 바뀌면 차트 폭도 맞춘다
    const handleResize = () => chart.applyOptions({ width: container.clientWidth });
    window.addEventListener("resize", handleResize);

    // 언마운트 시 정리 (메모리 누수 방지)
    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      volumeRef.current = null;
    };
  }, []);

  // 2) candles 가 바뀔 때마다: 캔들 + 거래량 데이터 갱신
  useEffect(() => {
    const series = seriesRef.current;
    const volume = volumeRef.current;
    if (!series || !volume) return;

    // ISO 문자열 -> 초 단위 UTC epoch 로 변환 (lightweight-charts 요구 형식)
    const data = candles.map((c) => ({
      time: (Date.parse(c.window_start) / 1000) as UTCTimestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));

    // 거래량 막대: 상승봉(close>=open)은 초록, 하락봉은 빨강 (반투명)
    const volData = candles.map((c) => ({
      time: (Date.parse(c.window_start) / 1000) as UTCTimestamp,
      value: c.volume,
      color: c.close >= c.open ? "rgba(38,166,154,0.5)" : "rgba(239,83,80,0.5)",
    }));

    series.setData(data);
    volume.setData(volData);
    // 최신 데이터가 잘 보이도록 시간축을 데이터에 맞춰준다
    chartRef.current?.timeScale().fitContent();
  }, [candles]);

  return <div ref={containerRef} style={{ width: "100%" }} />;
}
