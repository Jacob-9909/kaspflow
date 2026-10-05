/**
 * BacktestPanel: 백테스트 설정 + 결과 패널
 * ============================================================
 * [이 파일이 하는 일]
 *   1. 켜기/끄기 토글 (끄면 차트의 매수/매도 마커도 사라진다)
 *   2. 전략/봉 개수/자본/수수료/리스크(손절·익절·추격)/전략 파라미터 입력
 *      - 입력은 "초안(draft)" 상태로 두고 [적용] 을 눌러야 서버에 요청한다.
 *        (글자를 칠 때마다 요청이 나가지 않게 하기 위함)
 *   3. 결과: 성과 지표 카드 + 최근 거래 표
 *
 * [봉 간격]
 *   "몇 분마다 신호를 평가할지"는 상단 툴바의 '간격'을 따른다 (1m/5m/15m/1h/...).
 *   그래야 차트의 봉과 매수/매도 마커가 1:1 로 맞는다.
 */
import { useState } from "react";
import type { BacktestConfig, BacktestResult, StrategyInfo } from "../api";

interface Props {
  strategies: StrategyInfo[];
  interval: string;
  enabled: boolean;
  onToggle: (on: boolean) => void;
  config: BacktestConfig; // 현재 적용된 설정
  onApply: (cfg: BacktestConfig) => void;
  result: BacktestResult | null;
  loading: boolean;
  error: string | null;
}

// 차트에도 같은 수의 봉을 불러오므로 /ohlc 상한(1000) 안에서 고른다.
const BAR_OPTIONS = [200, 500, 1000];

const REASON_LABEL: Record<string, string> = {
  signal: "신호",
  stop_loss: "손절",
  take_profit: "익절",
  trailing_stop: "추격손절",
};

const signedPct = (v: number, digits = 2) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;
const plainPct = (v: number, digits = 1) => `${(v * 100).toFixed(digits)}%`;
const money = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 2 });
const priceFmt = (v: number) =>
  v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: v < 10 ? 4 : 2 });
const timeFmt = (sec: number) =>
  new Date(sec * 1000).toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });

const pctToInput = (v: number | null) => (v === null ? "" : String(+(v * 100).toFixed(4)));
const paramsToInput = (p: Record<string, number>) =>
  Object.fromEntries(Object.entries(p).map(([k, v]) => [k, String(v)]));

function Stat({ label, value, tone }: { label: string; value: string; tone?: "up" | "down" }) {
  return (
    <div className={tone ? `metric metric--${tone}` : "metric"}>
      <span className="metric__label">{label}</span>
      <span className={`metric__value${tone ? ` ${tone}` : ""}`}>{value}</span>
    </div>
  );
}

export function BacktestPanel({
  strategies,
  interval,
  enabled,
  onToggle,
  config,
  onApply,
  result,
  loading,
  error,
}: Props) {
  // ---- 입력 초안 ----
  const [strategy, setStrategy] = useState(config.strategy);
  const [limit, setLimit] = useState(config.limit);
  const [capital, setCapital] = useState(String(config.initialCapital));
  const [fee, setFee] = useState(String(config.feeBps));
  const [sl, setSl] = useState(pctToInput(config.stopLossPct));
  const [tp, setTp] = useState(pctToInput(config.takeProfitPct));
  const [ts, setTs] = useState(pctToInput(config.trailingStopPct));
  const [params, setParams] = useState<Record<string, string>>(paramsToInput(config.params));
  const [formError, setFormError] = useState<string | null>(null);

  const current = strategies.find((s) => s.id === strategy);

  const pickStrategy = (id: string) => {
    setStrategy(id);
    const info = strategies.find((s) => s.id === id);
    setParams(paramsToInput(info ? info.params : {})); // 전략을 바꾸면 그 전략의 기본값으로
    setFormError(null);
  };

  const apply = () => {
    const toNum = (s: string) => (s.trim() === "" ? null : Number(s));
    const capitalN = toNum(capital);
    const feeN = toNum(fee);
    if (capitalN === null || !Number.isFinite(capitalN) || capitalN <= 0) {
      return setFormError("초기 자본은 0보다 커야 합니다.");
    }
    if (feeN === null || !Number.isFinite(feeN) || feeN < 0 || feeN > 100) {
      return setFormError("수수료는 0~100 bp 범위여야 합니다.");
    }
    // 손절/익절/추격: 빈 칸 = 미사용, 입력은 % 단위 -> 서버는 비율
    const pct = (s: string, name: string): number | null | undefined => {
      const n = toNum(s);
      if (n === null) return null;
      if (!Number.isFinite(n) || n <= 0 || n >= 100) {
        setFormError(`${name} 은 0~100 사이의 % 값이어야 합니다 (빈 칸 = 사용 안 함).`);
        return undefined;
      }
      return n / 100;
    };
    const slN = pct(sl, "손절");
    const tpN = pct(tp, "익절");
    const tsN = pct(ts, "추격손절");
    if (slN === undefined || tpN === undefined || tsN === undefined) return;

    const paramsN: Record<string, number> = {};
    for (const [k, v] of Object.entries(params)) {
      const n = Number(v);
      if (v.trim() === "" || !Number.isFinite(n)) return setFormError(`파라미터 ${k} 를 숫자로 입력하세요.`);
      paramsN[k] = n;
    }

    setFormError(null);
    onApply({
      strategy,
      limit,
      initialCapital: capitalN,
      feeBps: feeN,
      stopLossPct: slN,
      takeProfitPct: tpN,
      trailingStopPct: tsN,
      params: paramsN,
    });
  };

  const m = result?.metrics;
  const recent = result ? [...result.trades].reverse().slice(0, 15) : [];

  return (
    <section className="bt">
      <div className="bt__head">
        <div>
          <h2 className="bt__title">백테스트</h2>
          <p className="bt__sub">
            midas-touch 전략 · <b>{interval}</b> 봉마다 신호 평가 (상단 '간격'에서 변경) · 종가 체결, 슬리피지 미반영
          </p>
        </div>
        <label className="switch">
          <input type="checkbox" checked={enabled} onChange={(e) => onToggle(e.target.checked)} />
          <span className="switch__track" />
          <span className="switch__label">{enabled ? "켜짐" : "꺼짐"}</span>
        </label>
      </div>

      {enabled && (
        <>
          <div className="bt__form">
            <div className="bt__row">
              <span className="toolbar__label">전략</span>
              <div className="symbol-selector">
                {strategies.map((s) => (
                  <button
                    key={s.id}
                    className={s.id === strategy ? "chip chip--sm chip--active" : "chip chip--sm"}
                    onClick={() => pickStrategy(s.id)}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="bt__row">
              <span className="toolbar__label">기간</span>
              <div className="symbol-selector">
                {BAR_OPTIONS.map((n) => (
                  <button
                    key={n}
                    className={n === limit ? "chip chip--sm chip--active" : "chip chip--sm"}
                    onClick={() => setLimit(n)}
                  >
                    최근 {n}봉
                  </button>
                ))}
              </div>
            </div>

            <div className="bt__fields">
              <label className="field">
                <span>초기 자본 (USDT)</span>
                <input inputMode="decimal" value={capital} onChange={(e) => setCapital(e.target.value)} />
              </label>
              <label className="field">
                <span>수수료 (bp, 편도)</span>
                <input inputMode="decimal" value={fee} onChange={(e) => setFee(e.target.value)} />
              </label>
              <label className="field">
                <span>손절 (%)</span>
                <input inputMode="decimal" placeholder="사용 안 함" value={sl} onChange={(e) => setSl(e.target.value)} />
              </label>
              <label className="field">
                <span>익절 (%)</span>
                <input inputMode="decimal" placeholder="사용 안 함" value={tp} onChange={(e) => setTp(e.target.value)} />
              </label>
              <label className="field">
                <span>추격손절 (%)</span>
                <input inputMode="decimal" placeholder="사용 안 함" value={ts} onChange={(e) => setTs(e.target.value)} />
              </label>
              {Object.keys(params).map((k) => (
                <label className="field field--param" key={`${strategy}:${k}`}>
                  <span>{k}</span>
                  <input
                    inputMode="decimal"
                    value={params[k]}
                    onChange={(e) => setParams((p) => ({ ...p, [k]: e.target.value }))}
                  />
                </label>
              ))}
              <button className="btn" onClick={apply}>
                적용
              </button>
            </div>
            {current && Object.keys(params).length === 0 && (
              <p className="muted">{current.label}은 하위 5개 전략의 기본 파라미터를 그대로 사용합니다 (3표 이상 강세면 진입).</p>
            )}
            {formError && <p className="error">⚠️ {formError}</p>}
          </div>

          {error && <p className="error">⚠️ 백테스트 실패: {error}</p>}
          {loading && !result && <p className="muted">백테스트 계산 중…</p>}

          {result && m && (
            <>
              <div className="metrics">
                <Stat
                  label={`전략 수익률 (${result.label})`}
                  value={signedPct(m.total_return)}
                  tone={m.total_return >= 0 ? "up" : "down"}
                />
                <Stat
                  label="단순 보유 수익률"
                  value={signedPct(m.buy_hold_return)}
                  tone={m.buy_hold_return >= 0 ? "up" : "down"}
                />
                <Stat label="최대 낙폭" value={signedPct(m.max_drawdown)} tone="down" />
                <Stat label="거래 횟수" value={`${m.trade_count}회`} />
                <Stat label="승률" value={plainPct(m.win_rate, 0)} />
                <Stat label="손익비 (PF)" value={m.profit_factor.toFixed(2)} />
                <Stat label="샤프" value={m.sharpe_ratio.toFixed(2)} />
                <Stat label="시장 노출도" value={plainPct(m.exposure_pct, 0)} />
                <Stat label="최종 자산 (USDT)" value={money(m.final_value)} />
              </div>

              <p className="bt__meta">
                {result.symbol} · {result.interval} · 집계 완료된 {result.bars}봉 기준 (진행 중인 마지막 봉 제외)
                {Object.keys(m.exit_reasons).length > 0 && (
                  <>
                    {" · 청산 "}
                    {Object.entries(m.exit_reasons)
                      .map(([k, v]) => `${REASON_LABEL[k] ?? k} ${v}`)
                      .join(" / ")}
                  </>
                )}
              </p>

              {result.open_position && (
                <p className="bt__open">
                  보유 중 · 진입 {timeFmt(result.open_position.entry_time)} @{" "}
                  {priceFmt(result.open_position.entry_price)} · 평가손익{" "}
                  <b className={result.open_position.unrealized_pct >= 0 ? "up" : "down"}>
                    {signedPct(result.open_position.unrealized_pct)}
                  </b>
                </p>
              )}

              {result.warnings.map((w) => (
                <p className="bt__warn" key={w}>
                  ⚠️ {w}
                </p>
              ))}

              {recent.length > 0 && (
                <div className="bt__table-wrap">
                  <table className="bt__table">
                    <thead>
                      <tr>
                        <th>진입</th>
                        <th>청산</th>
                        <th className="num">진입가</th>
                        <th className="num">청산가</th>
                        <th className="num">손익</th>
                        <th>사유</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recent.map((t) => (
                        <tr key={`${t.entry_time}-${t.exit_time}`}>
                          <td>{timeFmt(t.entry_time)}</td>
                          <td>{timeFmt(t.exit_time)}</td>
                          <td className="num">{priceFmt(t.entry_price)}</td>
                          <td className="num">{priceFmt(t.exit_price)}</td>
                          <td className={`num ${t.pnl_pct >= 0 ? "up" : "down"}`}>{signedPct(t.pnl_pct)}</td>
                          <td>{REASON_LABEL[t.exit_reason] ?? t.exit_reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {result.trades.length > recent.length && (
                    <p className="muted">최근 {recent.length}건만 표시 (전체 {result.trades.length}건)</p>
                  )}
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
