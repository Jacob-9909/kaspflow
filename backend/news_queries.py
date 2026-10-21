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

# 수집이 이 시간(분) 넘게 멈추면 이상으로 본다 (설계서 7절 모니터링).
# 2026-10-17~18(주말) 실측: 신선한 기사 사이 간격 p50 60분, p90 211분, 최대 320분 -> 30분은 오탐뿐이라 6시간.
# ponytail: 기사 도착 간격으로 수집 중단을 추정한다. 오탐·늦은 감지가 문제면 collector 폴링 성공 시각을 따로 남긴다.
HEALTH_STALE_MINUTES = 360
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
                   s.p_relevant, s.p_pos, s.p_neu, s.p_neg, s.event_type, s.p_event, s.model_version
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


def pipeline(conn, hours: int) -> dict[str, Any]:
    """'Laya 흐름' 패널용: 최근 hours 시간 동안 각 단계를 지난 건수와 가장 최근 정확도 측정.

    단계: 수집(news_raw) -> 종목 매칭 / 시장 전체 판정 -> Laya 분류(news_signal) -> 감정·이벤트 분포.
    매칭이 없고 MARKET 판정도 못 받은 기사는 신호가 없다 = '버려짐'.
    """
    mv = latest_model_version(conn)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT r.source, count(*) AS n,
                   count(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM news_signal s WHERE s.article_id = r.article_id AND s.model_version = %(mv)s
                   )) AS with_signal
            FROM news_raw r
            WHERE r.collected_at >= now() - make_interval(hours => %(h)s)
            GROUP BY r.source ORDER BY n DESC
            """,
            {"mv": mv, "h": hours},
        )
        sources = list(cur.fetchall())
        cur.execute(
            """
            SELECT s.symbol, count(*) AS n,
                   count(*) FILTER (WHERE s.p_pos >= s.p_neu AND s.p_pos >= s.p_neg) AS positive,
                   count(*) FILTER (WHERE s.p_neg >  s.p_pos AND s.p_neg >= s.p_neu) AS negative,
                   avg(s.p_pos - s.p_neg) AS avg_score,
                   count(*) FILTER (WHERE r.published_at IS NOT NULL
                                      AND s.collected_at - r.published_at <= interval '6 hours') AS fresh
            FROM news_signal s JOIN news_raw r USING (article_id)
            WHERE s.model_version = %(mv)s AND s.collected_at >= now() - make_interval(hours => %(h)s)
            GROUP BY s.symbol ORDER BY s.symbol
            """,
            {"mv": mv, "h": hours},
        )
        symbols = list(cur.fetchall())
        cur.execute(
            """
            SELECT coalesce(event_type, 'other') AS event_type, count(*) AS n
            FROM news_signal
            WHERE model_version = %(mv)s AND collected_at >= now() - make_interval(hours => %(h)s)
            GROUP BY 1 ORDER BY n DESC
            """,
            {"mv": mv, "h": hours},
        )
        events = list(cur.fetchall())
        # 처리 지연: 수집 -> 신호 저장 (최근 24시간). 소스를 늘렸을 때 Laya 가 밀리는지 본다.
        # 기사당 종목이 여럿이면 첫 신호 기준. --backfill 로 늦게 채운 행도 섞이므로 24시간으로 좁힌다.
        cur.execute(
            """
            SELECT count(*) AS n,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY lag) AS p50,
                   percentile_cont(0.9) WITHIN GROUP (ORDER BY lag) AS p90
            FROM (SELECT extract(epoch FROM min(created_at) - min(collected_at)) AS lag
                  FROM news_signal
                  WHERE model_version = %(mv)s AND collected_at >= now() - interval '24 hours'
                  GROUP BY article_id) t
            """,
            {"mv": mv},
        )
        lag = cur.fetchone()
        evals: list[dict[str, Any]] = []
        if mv is not None and _has_table(cur, "news_eval"):
            cur.execute(
                """
                SELECT run_at, labels, field, n, acc, baseline, ece_raw, ece_cal, temperature, passed
                FROM news_eval
                WHERE model_version = %s
                  AND run_at = (SELECT max(run_at) FROM news_eval WHERE model_version = %s)
                ORDER BY field
                """,
                (mv, mv),
            )
            evals = list(cur.fetchall())
    for s in symbols:
        s["neutral"] = s["n"] - s["positive"] - s["negative"]
        s["avg_score"] = None if s["avg_score"] is None else round(float(s["avg_score"]), 4)
    collected = sum(r["n"] for r in sources)
    with_signal = sum(r["with_signal"] for r in sources)
    return {
        "hours": hours,
        "model_version": mv,
        "collected": collected,
        "with_signal": with_signal,
        "dropped": collected - with_signal,
        "sources": sources,
        "symbols": symbols,
        "events": events,
        "lag_24h": {
            "n": lag["n"],
            "p50_s": None if lag["p50"] is None else round(float(lag["p50"])),
            "p90_s": None if lag["p90"] is None else round(float(lag["p90"])),
        },
        "eval": evals,
    }


def _has_table(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (name,))
    return bool(cur.fetchone()["ok"])
