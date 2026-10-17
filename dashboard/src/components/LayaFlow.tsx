/**
 * LayaFlow: 뉴스가 Laya 를 거쳐 전략 신호가 되기까지의 흐름
 * ============================================================
 * 1) 흐름: 수집 -> 종목 매칭 -> Laya 질문 -> 신호 저장 -> 전략 피처, 단계마다 최근 7일 건수 (/news/pipeline)
 * 2) 정확도: label.py eval --save 로 남긴 가장 최근 측정 (news_eval)
 * 3) 기사 추적: 뉴스 목록에서 고른 기사 1건이 각 단계에서 어떤 값을 받았는지
 *
 * 질문 문구는 signal-worker/questions.py (schema_version 1) 와 같아야 한다. 문구를 바꾸면 여기도 바꾼다.
 * 확률은 보정되지 않은 모델 출력이며 투자 조언이 아니다.
 */
import type { ReactNode } from "react";
import type { NewsItem, NewsPipeline } from "../api";
import { EVENT_LABEL } from "./NewsList";

const QUESTIONS = [
  { key: "relevance", q: "Is this article mainly about {coin}?", choices: "mainly about it / only mentions it / not about it" },
  { key: "sentiment", q: "What is the sentiment of this article toward {coin}?", choices: "positive / neutral / negative" },
  { key: "event_type", q: "What kind of event does this article report?", choices: "상장·상폐 / 규제 / 해킹 / 기관·ETF / 파트너십·기술 / 거시 / 기타" },
];

const FIELD_LABEL: Record<string, string> = { relevance: "관련도", sentiment: "감정", event_type: "사건 종류" };

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "-" : `${Math.round(v * 100)}%`);
const num = (v: number) => v.toLocaleString("ko-KR");

function Bar({ value, tone }: { value: number | null; tone?: "up" | "down" | "neutral" }) {
  return (
    <span className="laya__bar">
      <span className={`laya__bar-fill laya__bar-fill--${tone ?? "neutral"}`} style={{ width: `${Math.round((value ?? 0) * 100)}%` }} />
    </span>
  );
}

function Stage({ step, title, children }: { step: number; title: string; children: ReactNode }) {
  return (
    <div className="laya__stage">
      <div className="laya__stage-head">
        <span className="laya__step">{step}</span>
        <span className="laya__stage-title">{title}</span>
      </div>
      {children}
    </div>
  );
}

/** 이 기사가 전략에 쓰이는지: 발행 후 max_age_min 안에 수집됐고, use_rel=1 이면 관련도 기준도 넘어야 한다. */
function strategyUse(n: NewsItem, d: Record<string, number>): { ok: boolean; why: string } {
  if (!n.published_at) return { ok: false, why: "발행 시각이 없어 제외 (오래된 기사인지 알 수 없음)" };
  const ageMin = (Date.parse(n.collected_at) - Date.parse(n.published_at)) / 60000;
  if (ageMin > d.max_age_min) {
    return { ok: false, why: `발행 ${Math.round(ageMin / 60)}시간 뒤에 수집돼 제외 (기준 ${d.max_age_min / 60}시간: 이미 시장에 반영된 뉴스)` };
  }
  if (d.use_rel && (n.p_relevant ?? 0) < d.rel_th) return { ok: false, why: `관련도 ${pct(n.p_relevant)} < ${pct(d.rel_th)} 로 제외` };
  return { ok: true, why: `수집 후 ${d.lookback_min / 60}시간 동안 news_score 에 반영 (τ=${d.tau_min}분으로 감쇠)` };
}

function Trace({ n, d, onClose }: { n: NewsItem; d: Record<string, number>; onClose: () => void }) {
  const s = n.p_pos !== null && n.p_neg !== null ? n.p_pos - n.p_neg : null;
  const use = strategyUse(n, d);
  const events = Object.entries(n.p_event ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 3);
  const time = (iso: string | null) => (iso ? new Date(iso).toLocaleString("ko-KR", { hour12: false }) : "-");
  return (
    <div className="laya__trace">
      <div className="laya__trace-head">
        <h4>기사 1건 따라가기</h4>
        <button type="button" className="chip chip--sm" onClick={onClose}>닫기</button>
      </div>
      <a href={n.url} target="_blank" rel="noopener noreferrer" className="news__link">{n.title}</a>
      <ol className="laya__trace-steps">
        <li>
          <b>수집</b> {n.source} · 발행 {time(n.published_at)} · 수집 {time(n.collected_at)}
        </li>
        <li>
          <b>종목 매칭</b> {n.symbol === "MARKET"
            ? "종목 별칭이 없어 '시장 전체 기사인가'를 물었고, 90% 이상이라 MARKET 신호가 됨"
            : `제목·요약에서 ${n.symbol.replace("USDT", "")} 별칭을 찾아 이 종목으로 질문`}
        </li>
        <li>
          <b>Laya 답</b> ({n.model_version})
          <div className="laya__probs">
            <span>{n.symbol === "MARKET" ? "시장 전체" : "주 대상"}</span><Bar value={n.p_relevant} /><span>{pct(n.p_relevant)}{!d.use_rel && " · 전략 미사용"}</span>
            <span>호재</span><Bar value={n.p_pos} tone="up" /><span>{pct(n.p_pos)}</span>
            <span>중립</span><Bar value={n.p_neu} /><span>{pct(n.p_neu)}</span>
            <span>악재</span><Bar value={n.p_neg} tone="down" /><span>{pct(n.p_neg)}</span>
            {events.map(([k, v]) => (
              <span key={k} className="laya__probs-row">
                <span>{EVENT_LABEL[k] ?? k}</span><Bar value={v} /><span>{pct(v)}</span>
              </span>
            ))}
          </div>
        </li>
        <li>
          <b>기사 점수</b> s = 호재 − 악재 = {s === null ? "-" : <span className={s > 0 ? "up" : s < 0 ? "down" : ""}>{s.toFixed(2)}</span>}
        </li>
        <li className={use.ok ? "" : "muted"}>
          <b>전략 반영</b> {use.ok ? "예" : "아니오"} — {use.why}
        </li>
      </ol>
    </div>
  );
}

export function LayaFlow({ p, traced, onCloseTrace }: { p: NewsPipeline; traced: NewsItem | null; onCloseTrace: () => void }) {
  const d = p.strategy_defaults;
  const days = Math.round(p.hours / 24);
  const proxy = p.eval.length > 0 && p.eval[0].labels !== "news_label";
  const used = (field: string) => (field === "sentiment" ? "전략에 사용" : field === "relevance" && d.use_rel ? "전략에 사용" : "전략 미사용");
  return (
    <div className="news laya">
      <div className="news__head">
        <h3 className="news__title">Laya 흐름 · 뉴스가 신호가 되기까지</h3>
        <span className="muted">최근 {days}일 · 모델 {p.model_version ?? "-"}</span>
      </div>

      <div className="laya__flow">
        <Stage step={1} title="수집">
          <p className="laya__big">{num(p.collected)}건</p>
          <p className="muted">코인 매체 RSS 를 5분마다 폴링 → Kafka news-raw</p>
          <ul className="laya__list">
            {p.sources.map((s) => (
              <li key={s.source}><span>{s.source}</span><span>{num(s.n)}</span></li>
            ))}
          </ul>
        </Stage>

        <Stage step={2} title="종목 매칭">
          <p className="laya__big">{num(p.with_signal)}건 <span className="muted">통과</span></p>
          <p className="muted">
            제목·요약에서 BTC/Bitcoin, ETH/Ethereum/Ether, XRP/Ripple 을 찾는다. 없으면 "시장 전체 기사인가"를 묻고 90% 이상만 MARKET.
          </p>
          <p className="muted">버려짐 {num(p.dropped)}건 (종목도 시장 전체도 아님)</p>
        </Stage>

        <Stage step={3} title="Laya 질문">
          <p className="muted">종목마다 아래 3개를 한 번의 호출로 묻고 선택지별 확률을 받는다.</p>
          <ol className="laya__questions">
            {QUESTIONS.map((q) => (
              <li key={q.key}>
                <code>{q.q}</code>
                <span className="muted"> {q.choices}</span>
              </li>
            ))}
          </ol>
        </Stage>

        <Stage step={4} title="신호 (종목별)">
          <ul className="laya__list laya__list--sent">
            {p.symbols.map((s) => (
              <li key={s.symbol}>
                <span>{s.symbol === "MARKET" ? "시장 전체" : s.symbol.replace("USDT", "")} {num(s.n)}</span>
                <span className="laya__stack" title={`호재 ${s.positive} · 중립 ${s.neutral} · 악재 ${s.negative}`}>
                  <span className="laya__stack-up" style={{ flex: s.positive }} />
                  <span className="laya__stack-neu" style={{ flex: s.neutral }} />
                  <span className="laya__stack-down" style={{ flex: s.negative }} />
                </span>
              </li>
            ))}
          </ul>
          <p className="muted">
            사건: {p.events.slice(0, 4).map((e) => `${EVENT_LABEL[e.event_type] ?? e.event_type} ${e.n}`).join(" · ")}
          </p>
        </Stage>

        <Stage step={5} title="전략 (news_sentiment)">
          <p className="muted">
            기사 점수 s = 호재 − 악재. 봉 마감까지 수집된 기사를 τ={d.tau_min}분으로 감쇠해 평균 → news_score.
            {" "}≥ {d.buy_th} 진입, ≤ {d.sell_th} 청산, 기사가 식으면 청산.
          </p>
          <p className="muted">
            발행 {d.max_age_min / 60}시간 넘은 기사 제외 · 관련도 {d.use_rel ? `${pct(d.rel_th)} 이상만` : "사용 안 함"}
          </p>
          <ul className="laya__list">
            {p.symbols.map((s) => (
              <li key={s.symbol}>
                <span>{s.symbol === "MARKET" ? "시장 전체" : s.symbol.replace("USDT", "")} 반영 가능</span>
                <span>{num(s.fresh)} / {num(s.n)}</span>
              </li>
            ))}
          </ul>
        </Stage>
      </div>

      <div className="laya__eval">
        <h4>
          정확도 {p.eval[0] && <span className="muted">· {new Date(p.eval[0].run_at).toLocaleDateString("ko-KR")} · 라벨 {p.eval[0].n}쌍</span>}
        </h4>
        {p.eval.length === 0 ? (
          <p className="muted">아직 측정 기록이 없습니다 (signal-worker 에서 python label.py eval --save).</p>
        ) : (
          <>
            {proxy && <p className="laya__warn">사람이 아닌 대리 라벨({p.eval[0].labels})로 잰 잠정 값입니다. 게이트 판정에는 쓰지 않습니다.</p>}
            <table className="bt__table">
              <thead>
                <tr><th>질문</th><th>정확도</th><th>기준선</th><th>보정 후 ECE</th><th>전략</th></tr>
              </thead>
              <tbody>
                {p.eval.map((e) => (
                  <tr key={e.field}>
                    <td>{FIELD_LABEL[e.field] ?? e.field}</td>
                    <td className={e.acc > e.baseline ? "up" : "down"}>{e.acc.toFixed(2)}</td>
                    <td>{e.baseline.toFixed(2)}</td>
                    <td>{e.ece_cal === null ? "-" : e.ece_cal.toFixed(3)}</td>
                    <td>{used(e.field)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted">기준선 = 항상 가장 흔한 답만 고를 때의 정확도. 이보다 낮으면 그 질문은 쓸모가 없다.</p>
          </>
        )}
      </div>

      {traced ? (
        <Trace n={traced} d={d} onClose={onCloseTrace} />
      ) : (
        <p className="muted laya__hint">뉴스 목록에서 "흐름 보기"를 누르면 그 기사가 각 단계에서 받은 값을 보여 줍니다.</p>
      )}
    </div>
  );
}
