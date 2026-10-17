/**
 * NewsList: 뉴스 목록 (제목·링크·감정·이벤트)
 * ============================================================
 * 차트의 뉴스 점을 눌렀을 때(선택한 봉의 기사) 또는 최근 기사를 보여 준다.
 * 제목은 원문 링크이고, 점 색은 차트 마커와 같다 (초록 호재 / 회색 중립 / 빨강 악재).
 * 값은 모델이 낸 확률이며 투자 조언이 아니다.
 */
import type { NewsItem } from "../api";

const SENTIMENT_LABEL = { positive: "호재", neutral: "중립", negative: "악재" } as const;

// signal-worker/questions.py 의 EVENT_TYPES 와 같은 키
export const EVENT_LABEL: Record<string, string> = {
  listing_delisting: "상장·상폐",
  regulation_legal: "규제·법률",
  hack_security: "해킹·보안",
  institutional_etf_flow: "기관·ETF",
  partnership_tech: "파트너십·기술",
  macro: "거시경제",
  other: "기타",
};

const timeFmt = (iso: string) =>
  new Date(iso).toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });

const pct = (v: number | null) => (v === null ? "-" : `${Math.round(v * 100)}%`);

export function NewsList({
  items,
  title,
  hint,
  onTrace,
}: {
  items: NewsItem[];
  title: string;
  hint?: string;
  onTrace?: (item: NewsItem) => void; // "흐름 보기": Laya 흐름 패널에서 이 기사를 따라간다
}) {
  return (
    <div className="news">
      <div className="news__head">
        <h3 className="news__title">{title}</h3>
        {hint && <span className="muted">{hint}</span>}
      </div>
      {items.length === 0 ? (
        <p className="muted">표시할 뉴스가 없습니다.</p>
      ) : (
        <ul className="news__list">
          {items.map((n) => (
            <li className="news__item" key={`${n.article_id}:${n.symbol}`}>
              <span className={`news__dot news__dot--${n.sentiment ?? "neutral"}`} title={n.sentiment ? SENTIMENT_LABEL[n.sentiment] : ""} />
              <div className="news__body">
                <a href={n.url} target="_blank" rel="noopener noreferrer" className="news__link">
                  {n.title}
                </a>
                <div className="news__meta">
                  {n.source} · {timeFmt(n.collected_at)} 수집 · {n.symbol === "MARKET" ? "시장 전체" : n.symbol}
                  {n.sentiment && ` · ${SENTIMENT_LABEL[n.sentiment]} (+${pct(n.p_pos)} / -${pct(n.p_neg)})`}
                  {n.event_type && ` · ${EVENT_LABEL[n.event_type] ?? n.event_type}`}
                  <span className="news__rel" title="관련도 질문은 정확도가 낮아 전략에 쓰지 않는다 (Laya 흐름 패널 참고)">
                    {` · 관련도 ${pct(n.p_relevant)}`}
                  </span>
                  {onTrace && (
                    <button type="button" className="news__trace" onClick={() => onTrace(n)}>
                      흐름 보기
                    </button>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
