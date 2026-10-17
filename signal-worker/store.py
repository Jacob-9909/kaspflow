"""
Store: signal-worker 의 PostgreSQL 접근 (news_raw / news_signal)
============================================================
SQL 을 한곳에 모아 두고, 비즈니스 로직(main.process_message)은 이 클래스의 메서드만 부른다.
테스트는 실제 Postgres 에 붙어서 돈다 (TEST_DATABASE_URL, 없으면 건너뜀).

쓰기 규칙 (docs/news-signal-design.md 4.2절):
  - news_raw    : ON CONFLICT DO NOTHING. 같은 기사가 다시 와도 처음 값이 남는다.
  - news_signal : (article_id, symbol, model_version) 키로 upsert. 재처리해도 결과가 같다.
  - news_signal.collected_at 은 항상 news_raw 에 저장된 최초 값을 복사한다 (메시지 값이 아니다).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from psycopg.rows import dict_row

from classifier import Classification

REQUIRED = ("article_id", "source", "url", "title", "collected_at")


def dsn_from_env() -> str:
    return make_conninfo(
        host=os.getenv("POSTGRES_HOST", "host.docker.internal"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "crypto"),
        user=os.getenv("POSTGRES_USER", "crypto"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        connect_timeout="10",
    )


def _parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_message(raw: bytes | str) -> dict[str, Any]:
    """news-raw 메시지(JSON) -> 검증된 dict (시각은 datetime). 형식이 틀리면 ValueError."""
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"JSON 이 아닙니다: {e}") from e
    if not isinstance(msg, dict):
        raise ValueError("메시지는 JSON 객체여야 합니다")
    missing = [k for k in REQUIRED if not msg.get(k)]
    if missing:
        raise ValueError(f"필수 필드 누락: {missing}")
    try:
        collected = _parse_ts(msg["collected_at"])
        published = _parse_ts(msg.get("published_at"))
    except ValueError as e:
        raise ValueError(f"시각 형식 오류: {e}") from e
    return {
        "article_id": str(msg["article_id"]),
        "source": str(msg["source"]),
        "url": str(msg["url"]),
        "title": str(msg["title"]),
        "body": msg.get("body") or "",
        "lang": msg.get("lang") or None,
        "published_at": published,
        "collected_at": collected,
    }


class PgStore:
    def __init__(self, dsn: str) -> None:
        self.conn = psycopg.connect(dsn, autocommit=False)

    # -- 트랜잭션 --
    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # -- news_raw --
    def insert_raw(self, m: dict[str, Any]) -> tuple[bool, datetime]:
        """원문을 넣는다. (새로 넣었는지, news_raw 에 저장된 collected_at) 을 돌려준다."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO news_raw (article_id, source, url, title, body, lang, published_at, collected_at)
                VALUES (%(article_id)s, %(source)s, %(url)s, %(title)s, %(body)s, %(lang)s, %(published_at)s, %(collected_at)s)
                ON CONFLICT (article_id) DO NOTHING
                RETURNING collected_at
                """,
                m,
            )
            row = cur.fetchone()
            if row is not None:
                return True, row[0]
            cur.execute("SELECT collected_at FROM news_raw WHERE article_id = %s", (m["article_id"],))
            return False, cur.fetchone()[0]

    # -- news_signal --
    def has_signal(self, article_id: str, model_version: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM news_signal WHERE article_id = %s AND model_version = %s LIMIT 1",
                (article_id, model_version),
            )
            return cur.fetchone() is not None

    def upsert_signals(
        self, article_id: str, collected_at: datetime, model_version: str,
        results: list[tuple[str, Classification]],
    ) -> int:
        with self.conn.cursor() as cur:
            for symbol, c in results:
                cur.execute(
                    """
                    INSERT INTO news_signal
                        (article_id, symbol, model_version, collected_at,
                         p_relevant, p_pos, p_neu, p_neg, event_type, p_event)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (article_id, symbol, model_version) DO UPDATE SET
                        collected_at = EXCLUDED.collected_at,
                        p_relevant = EXCLUDED.p_relevant,
                        p_pos = EXCLUDED.p_pos, p_neu = EXCLUDED.p_neu, p_neg = EXCLUDED.p_neg,
                        event_type = EXCLUDED.event_type, p_event = EXCLUDED.p_event
                    """,
                    (article_id, symbol, model_version, collected_at,
                     c.p_relevant, c.p_pos, c.p_neu, c.p_neg, c.event_type, Jsonb(c.p_event)),
                )
        return len(results)

    def unclassified(self, model_version: str, limit: int, after: str = "") -> list[dict[str, Any]]:
        """이 model_version 의 신호가 하나도 없는 원문 (오래된 것부터, article_id 커서 페이지네이션).

        --backfill 이 쓴다. 종목 매칭이 없는 기사는 신호가 안 생겨서 다시 나올 수 있다 -> after 커서로 한 번만 훑는다.
        """
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT r.article_id, r.title, r.body, r.collected_at
                FROM news_raw r
                WHERE r.article_id > %s
                  AND NOT EXISTS (SELECT 1 FROM news_signal s
                                  WHERE s.article_id = r.article_id AND s.model_version = %s)
                ORDER BY r.article_id
                LIMIT %s
                """,
                (after, model_version, limit),
            )
            return list(cur.fetchall())
