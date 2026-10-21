/**
 * NewsScoreChart: 캔들 차트 아래에 붙는 news_score 보조 선
 * ============================================================
 * [이 파일이 하는 일]
 *   백엔드 /news/score 가 계산한 봉별 news_score(-1~1)를 선으로 그린다.
 *   0 이 중립이고, 위(초록)는 호재 쪽, 아래(빨강)는 악재 쪽이다.
 *   값은 백테스트 전략(news_sentiment)이 쓰는 것과 같은 피처라서, 매수/매도 마커가 왜 거기 찍혔는지 눈으로 확인할 수 있다.
 *
 * 전략 기본값(d)을 받으면 진입(buy_th)·청산(sell_th) 기준선을 긋고, 가중치가 min_weight 미만인 봉(= 전략상 '신호 없음')은
 * 선을 끊는다. 신호가 없는 봉을 0 으로 이어 그리면 '중립 뉴스'처럼 보이기 때문이다.
 *
 * [시간축]
 *   메인 캔들 차트가 보내 주는 '보이는 시간 구간'을 그대로 따라간다 (방향은 메인 -> 보조 한쪽뿐이라 되돌이 루프가 없다).
 *   가격축 폭(minimumWidth)을 메인 차트와 같게 맞춰 두 차트의 시간축이 세로로 정렬된다.
 */
import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { NewsScorePoint } from "../api";

interface Props {
  series: NewsScorePoint[];
  // 메인 차트의 보이는 시간 구간 (UTC epoch 초). null 이면 맞추지 않는다.
  range: { from: number; to: number } | null;
  // 다른 차트(심볼/간격)로 바뀌면 시간축을 처음부터 다시 맞춘다.
  viewKey: string;
  // news_sentiment 기본 파라미터 (/news/pipeline 의 strategy_defaults). 없으면 기준선·끊김 없이 그린다.
  d?: Record<string, number>;
}

export function NewsScoreChart({ series, range, viewKey, d }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const lineRef = useRef<ISeriesApi<"Line"> | null>(null);
  const rangeRef = useRef(range);
  rangeRef.current = range;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chart = createChart(container, {
      layout: { background: { type: ColorType.Solid, color: "#0e1117" }, textColor: "#d1d4dc" },
      grid: { vertLines: { color: "#1e222d" }, horzLines: { color: "#1e222d" } },
      // 시간 라벨은 메인 차트가 보여 주므로 여기서는 숨겨 높이를 아낀다.
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#2a2e39", visible: false },
      rightPriceScale: { borderColor: "#2a2e39", minimumWidth: 72 },
      // 보조 차트는 메인을 따라가기만 한다 (직접 끌거나 확대하면 둘이 어긋난다)
      handleScroll: false,
      handleScale: false,
      height: 130,
      autoSize: true,
    });
    const line = chart.addLineSeries({
      color: "#2dd4bf",
      lineWidth: 2,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
      lastValueVisible: true,
      priceLineVisible: false,
    });
    line.createPriceLine({ price: 0, color: "#4b5565", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "" });
    chartRef.current = chart;
    lineRef.current = line;
    return () => {
      chart.remove();
      chartRef.current = null;
      lineRef.current = null;
    };
  }, []);

  // 데이터 갱신 (다른 차트로 바뀌면 viewKey 가 달라져 이 effect 가 다시 돌고, 시간축은 아래 effect 가 맞춘다)
  useEffect(() => {
    const line = lineRef.current;
    if (!line) return;
    const minW = d?.min_weight ?? 0;
    line.setData(
      series.map((p) =>
        p.weight >= minW ? { time: p.time as UTCTimestamp, value: p.score } : { time: p.time as UTCTimestamp },
      ),
    );
  }, [series, viewKey, d?.min_weight]);

  // 진입·청산 기준선
  useEffect(() => {
    const line = lineRef.current;
    if (!line || d?.buy_th === undefined || d?.sell_th === undefined) return;
    const lines = [
      line.createPriceLine({ price: d.buy_th, color: "#26a69a", lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: "진입" }),
      line.createPriceLine({ price: d.sell_th, color: "#ef5350", lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: "청산" }),
    ];
    return () => lines.forEach((l) => line.removePriceLine(l));
  }, [d?.buy_th, d?.sell_th]);

  // 메인 차트의 시간 구간을 따라간다
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !range || series.length === 0) return;
    try {
      chart.timeScale().setVisibleRange({ from: range.from as UTCTimestamp, to: range.to as UTCTimestamp });
    } catch {
      /* 구간이 데이터 밖이면 무시 */
    }
  }, [range, series, viewKey]);

  return <div ref={containerRef} style={{ width: "100%" }} />;
}
