"""
뉴스 신호 조회 쿼리 (news_raw / news_signal)
============================================================
app.py 의 엔드포인트가 쓰는 SQL 을 모아 두었다. 함수는 psycopg 연결을 인자로 받아서
커넥션 풀이 없어도 (테스트에서) 실제 PostgreSQL 에 직접 붙여 검증할 수 있다.

모델 버전: news_signal 은 (기사, 종목, model_version) 단위라 모델/질문을 바꾸면 같은 기사의 행이 여러 개다.
          따로 지정하지 않으면 '가장 최근에 쓰인' model_version 하나만 쓴다 (버전이 섞여 기사 수가 부풀지 않게).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.rows import dict_row

# 수집이 이 시간(분) 넘게 멈추면 이상으로 본다 (설계서 7절 모니터링)
HEALTH_STALE_MINUTES = 30
MARKET_SYMBOL = "MARKET"
SIGNAL_ROW_LIMIT = 20_000  # /backtest, /features 가 한 번에 읽는 신호 상한 (공개 엔드포인트 보호)


def latest_model_version(conn) -> str | None:
    row = conn.execute("SELECT model_version FROM news_signal ORDER BY created_at DESC LIMIT 1").fetchone()
    return row[0] if row else None


def sentiment_label(p_pos: float | None, p_neu: float | None, p_neg: float | None) -> str | None:
    """가장 확률이 큰 감정 (대시보드 마커 색용)."""
    if p_pos is None or p_neu is None or p_neg is None:
        return None
    best = max((p_pos, "positive"), (p_neu, "neutral"), (p_neg, "negative"))
    return best[1]


def fetch_signals(
    conn, symbol: str, since: datetime, model_version: str | None = None, limit: int = SIGNAL_ROW_LIMIT,
) -> list[dict[str, Any]]:
    """피처 계산용 신호: 이 종목 + MARKET, collected_at >= since. published_at 은 news_raw 에서 가져온다."""
    mv = model_version or latest_model_version(conn)
    if mv is None:
        return []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT s.symbol, s.collected_at, r.published_at,
                   s.p_relevant, s.p_pos, s.p_neu, s.p_neg, s.event_type
            FROM news_signal s
            JOIN news_raw r USING (article_id)
            WHERE s.model_version = %s
              AND s.symbol IN (%s, %s)
              AND s.collected_at >= %s
            ORDER BY s.collected_at
            LIMIT %s
            """,
            (mv, symbol, MARKET_SYMBOL, since, limit),
        )
        return list(cur.fetchall())


def recent_news(
    conn, symbol: str | None, limit: int, since: datetime | None = None, model_version: str | None = None,
) -> list[dict[str, Any]]:
    """표시용: 최근 신호와 제목·링크·확률 (최신순)."""
    mv = model_version or latest_model_version(conn)
    if mv is None:
        return []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT s.article_id, s.symbol, r.source, r.title, r.url, r.published_at, s.collected_at,
                   s.p_relevant, s.p_pos, s.p_neu, s.p_neg, s.event_type, s.model_version
            FROM news_signal s
            JOIN news_raw r USING (article_id)
            WHERE s.model_version = %(mv)s
              AND (%(sym)s::text IS NULL OR s.symbol = %(sym)s)
              AND (%(since)s::timestamptz IS NULL OR s.collected_at >= %(since)s)
            ORDER BY s.collected_at DESC, s.article_id
            LIMIT %(lim)s
            """,
            {"mv": mv, "sym": symbol, "since": since, "lim": limit},
        )
        rows = list(cur.fetchall())
    for r in rows:
        r["sentiment"] = sentiment_label(r["p_pos"], r["p_neu"], r["p_neg"])
    return rows


def health(conn) -> dict[str, Any]:
    """마지막 수집 시각과 경과 시간. 수집 중단 감지용 (status: ok | stale | empty)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT max(collected_at) AS last_collected_at,
                   EXTRACT(EPOCH FROM (now() - max(collected_at))) / 60.0 AS age_minutes,
                   count(*) FILTER (WHERE collected_at >= now() - interval '24 hours') AS articles_24h
            FROM news_raw
            """
        )
        raw = cur.fetchone()
        cur.execute("SELECT count(*) AS n FROM news_signal WHERE collected_at >= now() - interval '24 hours'")
        signals_24h = cur.fetchone()["n"]
    last = raw["last_collected_at"]
    age = None if raw["age_minutes"] is None else round(float(raw["age_minutes"]), 1)
    if last is None:
        status = "empty"
    else:
        status = "stale" if age is not None and age > HEALTH_STALE_MINUTES else "ok"
    return {
        "status": status,
        "last_collected_at": last,
        "age_minutes": age,
        "stale_after_minutes": HEALTH_STALE_MINUTES,
        "articles_24h": raw["articles_24h"],
        "signals_24h": signals_24h,
        "model_version": latest_model_version(conn),
    }
