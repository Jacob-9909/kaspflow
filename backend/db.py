"""PostgreSQL 접근 (psycopg 3 풀). 캐시 읽기·쓰기가 실패해도 분석은 계속한다 (캐시 없이 다시 받을 뿐)."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

log = logging.getLogger("kaspflow.db")
SCHEMA = Path(__file__).with_name("schema.sql")


def conninfo() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    e = os.environ.get
    return (f"host={e('POSTGRES_HOST', 'localhost')} port={e('POSTGRES_PORT', '5432')} dbname={e('POSTGRES_DB', 'crypto')} "
            f"user={e('POSTGRES_USER', 'crypto')} password={e('POSTGRES_PASSWORD', '')} connect_timeout=5")


class DB:
    def __init__(self, dsn: str | None = None):
        self.pool = ConnectionPool(dsn or conninfo(), min_size=1, max_size=6, open=False,
                                   kwargs={"autocommit": True}, timeout=10)

    def open(self) -> None:
        self.pool.open(wait=False)

    def close(self) -> None:
        self.pool.close()

    def init_schema(self) -> None:
        with self.pool.connection() as c:
            c.execute(SCHEMA.read_text())
            c.execute("DELETE FROM stock.cache WHERE fetched_at < now() - interval '30 days'")

    def ping(self) -> bool:
        try:
            with self.pool.connection(timeout=3) as c:
                c.execute("SELECT 1 FROM stock.analysis_run LIMIT 1")
            return True
        except Exception as exc:  # noqa: BLE001 - 헬스체크용
            log.warning("db ping failed: %r", exc)
            return False

    # --- 캐시 (market.Cache 프로토콜) ---
    def get(self, key: str, max_age_s: int):
        try:
            with self.pool.connection() as c:
                row = c.execute("SELECT payload FROM stock.cache WHERE key = %s AND fetched_at > now() - make_interval(secs => %s)",
                                (key, max_age_s)).fetchone()
            return row[0] if row else None
        except Exception as exc:  # noqa: BLE001
            log.warning("cache get %s: %r", key, exc)
            return None

    def set(self, key: str, payload) -> None:
        try:
            with self.pool.connection() as c:
                c.execute("INSERT INTO stock.cache (key, payload, fetched_at) VALUES (%s, %s, now()) "
                          "ON CONFLICT (key) DO UPDATE SET payload = EXCLUDED.payload, fetched_at = now()", (key, Jsonb(payload)))
        except Exception as exc:  # noqa: BLE001
            log.warning("cache set %s: %r", key, exc)

    # --- 실행 기록 ---
    def save_run(self, run_id: str, r: dict) -> None:
        v = r["verdict"]
        with self.pool.connection() as c:
            c.execute("INSERT INTO stock.analysis_run (id, symbol, name, horizon, verdict, score, confidence, price, duration_ms, result) "
                      "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                      (run_id, r["symbol"], r["name"], r["horizon"], v["verdict"], v["score"], v["confidence"],
                       r["quote"]["price"], r["meta"]["duration_ms"], Jsonb(r)))

    def list_runs(self, symbol: str | None, limit: int) -> list[dict]:
        sql = ("SELECT id, symbol, name, created_at, horizon, verdict, score, confidence, price, "
               "result->'narrative'->>'headline', result->'position' FROM stock.analysis_run "
               + ("WHERE symbol = %s " if symbol else "") + "ORDER BY created_at DESC LIMIT %s")
        args = (symbol, limit) if symbol else (limit,)
        with self.pool.connection() as c:
            rows = c.execute(sql, args).fetchall()
        keys = ("id", "symbol", "name", "created_at", "horizon", "verdict", "score", "confidence", "price", "headline", "position")
        return [dict(zip(keys, row)) for row in rows]

    def get_run(self, run_id: str) -> dict | None:
        with self.pool.connection() as c:
            row = c.execute("SELECT result, created_at FROM stock.analysis_run WHERE id = %s", (run_id,)).fetchone()
        if not row:
            return None
        return {**row[0], "run_id": run_id, "created_at": row[1].isoformat()}
