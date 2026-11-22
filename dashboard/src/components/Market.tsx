/**
 * 시장 정세: 시장 국면(판단 엔진과 같은 계산), 지수와 다른 자산의 흐름, 업종 순위, 관심 종목 한눈에 보기.
 * 종목 하나를 보기 전에 "지금 시장이 어떤 때인가" 와 "내 종목들이 어디에 서 있는가" 를 한 화면에서 본다.
 * 색은 다른 화면과 같다: 오름·매수 쪽 빨강, 내림·매도 쪽 파랑, 나머지 무채색. 숫자는 늘 글자로도 보인다.
 */
import { useEffect, useState } from "react";
import { api, type MarketOverview, type MarketRow, type WatchOverviewItem } from "../api";
import { HORIZONS, HORIZON_SHORT, date, dirClass, num, pct, signed, usd } from "../format";
import { GROUPS, ModuleLead, groupScore } from "./Report";
import { DivBars, SegBar, ZoneScale } from "./Viz";

const PERIODS = [["d1", "1일"], ["w1", "1주"], ["m1", "1개월"], ["m3", "3개월"], ["ytd", "연초 이후"]] as const;
type Period = (typeof PERIODS)[number][0];

/** 6개월 흐름 선. 도형만 늘이고(글자 없음) 방향은 색으로, 값은 옆 칸의 숫자로 읽는다 */
function Spark({ pts }: { pts: number[] }) {
  if (pts.length < 2) return null;
  const lo = Math.min(...pts), span = Math.max(...pts) - lo || 1;
  const d = pts.map((v, i) => `${i ? "L" : "M"}${((i / (pts.length - 1)) * 100).toFixed(1)},${((1 - (v - lo) / span) * 100).toFixed(1)}`).join("");
  return (
    <svg className={`spark ${dirClass(pts[pts.length - 1] / pts[0] - 1, 0.002)}`} viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
      <path d={d} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function PriceRows({ title, rows }: { title: string; rows: MarketRow[] }) {
  return (
    <tbody>
      <tr className="group"><th scope="rowgroup" colSpan={9}>{title}</th></tr>
      {rows.map((r) => (
        <tr key={r.symbol}>
          <th scope="row"><span className="strat-name">{r.name}</span><div className="origin">{r.symbol}</div></th>
          <td className="num">{usd(r.price)}</td>
          <td className="spark-cell"><Spark pts={r.spark} /></td>
          {PERIODS.map(([k]) => <td key={k} className={`num ${dirClass(r[k], 0.0005)}`}>{pct(r[k])}</td>)}
          <td className={`num ${dirClass(r.vs_200, 0.0005)}`}>{pct(r.vs_200)}</td>
        </tr>
      ))}
    </tbody>
  );
}

/** 모듈 열 머리에 쓰는 짧은 이름 (칸이 좁다. 전체 이름은 title 로) */
const SHORT: Record<string, string> = { valuation: "가치", quality: "재무", consensus: "애널", personas: "대가",
  technical: "기술", strategies: "전략", analogs: "유사", sentiment: "뉴스", regime: "시장" };
const FULL: Record<string, string> = { valuation: "밸류에이션", quality: "재무 품질", consensus: "애널리스트 컨센서스", personas: "투자 대가 패널",
  technical: "기술적 지표", strategies: "검증된 전략 신호", analogs: "과거 유사 국면", sentiment: "뉴스 감성", regime: "시장 국면" };

/** 점수 칸: 바탕이 방향과 크기를 맡고(±100 에서 가장 진함) 글자는 잉크로 둔다 */
function Heat({ score, strong = false }: { score: number | null; strong?: boolean }) {
  if (score === null) return <td className="num heat na">-</td>;
  const mix = Math.round(Math.min(Math.abs(score), 1) * 42);
  return (
    <td className={`num heat${strong ? " strong" : ""}`} style={{ background: `color-mix(in oklab, var(${score >= 0 ? "--up" : "--down"}) ${mix}%, transparent)` }}>
      {signed(score * 100, 0)}
    </td>
  );
}

function WatchMatrix({ items, onOpen, onPick }: { items: WatchOverviewItem[]; onOpen: (id: string) => void; onPick: (s: string) => void }) {
  if (!items.length) return <p className="hint">관심 종목이 없습니다. 종목 화면에서 '관심 종목 추가'를 누르면 평일 장 마감 뒤 자동으로 판단해 여기에 모읍니다.</p>;
  const rows = [...items].sort((a, b) => (b.verdicts[b.horizon]?.score ?? -9) - (a.verdicts[a.horizon]?.score ?? -9));
  return (
    <div className="table-wrap">
      <table className="table compact matrix">
        <caption className="sr-only">관심 종목별 기간 판단과 모듈 점수</caption>
        <thead>
          <tr>
            <th rowSpan={2}>종목</th><th colSpan={3} className="span">판단</th><th rowSpan={2}>지금 할 일</th>
            {GROUPS.map((g) => <th key={g.key} colSpan={g.mods.length + 1} className="span">{g.label}</th>)}
          </tr>
          <tr>
            {HORIZONS.map((h) => <th key={h}>{HORIZON_SHORT[h]}</th>)}
            {GROUPS.flatMap((g) => [<th key={g.key} className="sum">종합</th>, ...g.mods.map((k) => <th key={k} title={FULL[k]}>{SHORT[k]}</th>)])}
          </tr>
        </thead>
        <tbody>
          {rows.map((x) => {
            const open = () => (x.run_id ? onOpen(x.run_id) : onPick(x.symbol));
            const mod = (k: string) => x.modules.find((m) => m.key === k);
            return (
              <tr key={x.symbol} className="clickable" tabIndex={0} onClick={open} onKeyDown={(e) => { if (e.key === "Enter") open(); }}>
                <th scope="row">
                  <span className="strat-name">{x.symbol}</span>
                  <div className="origin">{x.price === null ? "첫 판단 전" : <>{usd(x.price)} <span className={`num ${dirClass(x.change, 0.0005)}`}>{pct(x.change)}</span></>}</div>
                </th>
                {HORIZONS.map((h) => {
                  const v = x.verdicts[h];
                  return <td key={h}>{v ? <span className={`vbadge v-${v.verdict}`}>{v.label}</span> : "-"}</td>;
                })}
                <td className="l">{x.action ?? "-"}</td>
                {GROUPS.flatMap((g) => {
                  const cards = g.mods.map(mod).filter((m): m is NonNullable<typeof m> => !!m);
                  return [<Heat key={g.key} strong score={cards.length ? groupScore(cards) : null} />,
                    ...g.mods.map((k) => { const m = mod(k); return <Heat key={k} score={m?.available ? m.score : null} />; })];
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function Market({ onOpen, onPick }: { onOpen: (id: string) => void; onPick: (s: string) => void }) {
  const [m, setM] = useState<MarketOverview | null>(null);
  const [watch, setWatch] = useState<WatchOverviewItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [period, setPeriod] = useState<Period>("m1");
  const load = () => {
    setErr(null);
    api.market().then(setM).catch((e) => setErr((e as Error).message));
    api.watchOverview().then((w) => setWatch(w.items)).catch(() => setWatch([]));
  };
  useEffect(load, []);

  const spy = m?.indexes.find((x) => x.symbol === "SPY");
  const sectors = m ? [...m.sectors].sort((a, b) => (b[period] ?? -9) - (a[period] ?? -9)) : [];
  return (
    <div className="market">
      <section className="hero left">
        <h1>시장 정세</h1>
        <p>{m ? `${date(m.as_of)} 종가 기준. 종목을 보기 전에 지금 시장이 어떤 때인지, 관심 종목들이 어디에 서 있는지 봅니다.` : "지수, 변동성, 금리, 업종 흐름을 모으는 중입니다."}</p>
      </section>
      {err && (
        <div className="banner error" role="alert"><span>{err}</span><button className="btn ghost" onClick={load}>다시 시도</button></div>
      )}
      {!m && !err && <div className="skeleton block" aria-busy="true" />}
      {m && (
        <div className="stack">
          <section className="card" aria-labelledby="mk-regime">
            <h2 id="mk-regime">시장 국면</h2>
            <ModuleLead k="regime" m={m.regime} />
            <div className="viz-grid mk-gauges">
              {m.vix && <ZoneScale label="VIX 시장 불안" value={m.vix.value} min={0} max={50} fmt={(x) => num(x, 0)}
                                   zones={[{ to: 18, label: "안정" }, { to: 25, label: "보통" }, { to: 32, label: "높음" }, { to: 50, label: "매우 높음" }]} />}
              {spy?.vs_200 != null && <ZoneScale label="S&P 500, 200일선 대비" value={spy.vs_200} min={-0.2} max={0.2} fmt={(x) => pct(x, 0)}
                                                  zones={[{ to: 0, label: "200일선 아래" }, { to: 0.2, label: "200일선 위" }]} />}
              {m.tnx?.change_3m != null && <ZoneScale label={`미국 10년 금리 ${num(m.tnx.value, 2)}%, 3개월 변화`} value={m.tnx.change_3m} min={-1} max={1}
                                                        fmt={(x) => `${signed(x, 1)}%p`}
                                                        zones={[{ to: -0.5, label: "빠르게 내림" }, { to: 0.5, label: "큰 변화 없음" }, { to: 1, label: "빠르게 오름" }]} />}
              <SegBar label={`200일선 위에 있는 업종 (${m.breadth.total}개 중)`} unit={(p) => `${p.value}개`} parts={[
                { key: "below", label: "아래", value: m.breadth.total - m.breadth.above_200, tone: "down" },
                { key: "above", label: "위", value: m.breadth.above_200, tone: "up" }]} />
            </div>
          </section>

          <div className="mk-row">
            <section className="card" aria-labelledby="mk-prices">
              <h2 id="mk-prices">지수와 다른 자산</h2>
              <div className="table-wrap">
                <table className="table compact mk-table">
                  <caption className="sr-only">지수와 자산의 가격, 6개월 흐름, 기간별 수익률, 200일선 대비 위치</caption>
                  <thead><tr><th>이름</th><th>가격</th><th>6개월 흐름</th>{PERIODS.map(([k, label]) => <th key={k}>{label}</th>)}<th>200일선 대비</th></tr></thead>
                  <PriceRows title="지수" rows={m.indexes} />
                  <PriceRows title="다른 자산" rows={m.assets} />
                </table>
              </div>
            </section>

            <section className="card levels-map" aria-labelledby="mk-sectors">
              <div className="panel-head">
                <h2 id="mk-sectors">업종 순위</h2>
                <div className="segmented seg-5" role="radiogroup" aria-label="수익률 기간">
                  {PERIODS.map(([k, label]) => (
                    <label key={k} className={period === k ? "on" : ""}>
                      <input type="radio" name="sector-period" value={k} checked={period === k} onChange={() => setPeriod(k)} />{label}
                    </label>
                  ))}
                </div>
              </div>
              <DivBars label={`업종 ETF ${PERIODS.find(([k]) => k === period)?.[1]} 수익률`} fmt={(x) => pct(x)}
                       rows={sectors.map((s) => ({ key: s.symbol, value: s[period],
                         label: <>{s.name}<small>{s.symbol}, 200일선 {(s.vs_200 ?? 0) > 0 ? "위" : "아래"}</small></> }))} />
            </section>
          </div>

          <section className="card" aria-labelledby="mk-watch">
            <h2 id="mk-watch">관심 종목 한눈에 <span className="sub">가장 최근 판단. 칸의 숫자는 -100(매도 쪽)부터 +100(매수 쪽), 줄을 누르면 그 결과가 열립니다</span></h2>
            {watch ? <WatchMatrix items={watch} onOpen={onOpen} onPick={onPick} /> : <p className="hint">불러오는 중…</p>}
          </section>
        </div>
      )}
    </div>
  );
}
