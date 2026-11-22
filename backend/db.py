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


CACHE_WAIT_S = 3.0   # 캐시 읽기·쓰기가 DB 연결을 기다리는 시간. 넘으면 캐시 없이 간다 (풀 기본값 10초면 DB 장애 때 분석이 호출마다 멈춘다)


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
        self.prune_cache()

    def prune_cache(self) -> None:
        """한 달 넘은 캐시를 지운다 (기동할 때와 자동 판단 루프가 돌 때마다)."""
        with self.pool.connection() as c:
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
            with self.pool.connection(timeout=CACHE_WAIT_S) as c:
                row = c.execute("SELECT payload FROM stock.cache WHERE key = %s AND fetched_at > now() - make_interval(secs => %s)",
                                (key, max_age_s)).fetchone()
            return row[0] if row else None
        except Exception as exc:  # noqa: BLE001
            log.warning("cache get %s: %r", key, exc)
            return None

    def set(self, key: str, payload) -> None:
        try:
            with self.pool.connection(timeout=CACHE_WAIT_S) as c:
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

    def list_runs(self, symbol: str | None, limit: int, source: str | None = None) -> list[dict]:
        where, args = [], []
        if symbol:
            where.append("symbol = %s")
            args.append(symbol)
        if source:   # 자동 판단은 meta.source 가 'auto', 직접 실행은 값이 없다
            where.append("result->'meta'->>'source' " + ("= 'auto'" if source == "auto" else "IS DISTINCT FROM 'auto'"))
        sql = ("SELECT id, symbol, name, created_at, horizon, verdict, score, confidence, price, "
               "result->'narrative'->>'headline', result->'position', result->'meta'->>'source' FROM stock.analysis_run "
               + ("WHERE " + " AND ".join(where) + " " if where else "") + "ORDER BY created_at DESC LIMIT %s")
        args = (*args, limit)
        with self.pool.connection() as c:
            rows = c.execute(sql, args).fetchall()
        keys = ("id", "symbol", "name", "created_at", "horizon", "verdict", "score", "confidence", "price", "headline", "position", "source")
        return [dict(zip(keys, row)) for row in rows]

    def get_run(self, run_id: str) -> dict | None:
        with self.pool.connection() as c:
            row = c.execute("SELECT result, created_at FROM stock.analysis_run WHERE id = %s", (run_id,)).fetchone()
        if not row:
            return None
        return {**row[0], "run_id": run_id, "created_at": row[1].isoformat()}

    # --- 관심 종목 ---
    def list_watchlist(self) -> list[dict]:
        """관심 종목과 각 종목·기간의 최근 실행 두 건 (마지막 판단과 그 전 판단: 판단이 바뀌었는지 본다)."""
        sql = ("SELECT w.symbol, w.horizon, w.added_at, r.id, r.created_at, r.verdict, r.score, r.name "
               "FROM stock.watchlist w LEFT JOIN LATERAL (SELECT id, created_at, verdict, score, name FROM stock.analysis_run a "
               "WHERE a.symbol = w.symbol AND a.horizon = w.horizon ORDER BY created_at DESC LIMIT 2) r ON true "
               "ORDER BY w.added_at, w.symbol, r.created_at DESC")
        with self.pool.connection() as c:
            rows = c.execute(sql).fetchall()
        out: dict[str, dict] = {}
        for sym, hz, added, rid, at, verdict, score, name in rows:
            w = out.setdefault(sym, {"symbol": sym, "horizon": hz, "added_at": added, "name": None, "last": None, "prev": None})
            if rid is None:
                continue
            run = {"id": str(rid), "created_at": at, "verdict": verdict, "score": score}
            if w["last"] is None:
                w["last"], w["name"] = run, name
            else:
                w["prev"] = run
        return list(out.values())

    def watch_overview(self) -> list[dict]:
        """관심 종목마다 가장 최근 판단(같은 종목·기간)에서 화면에 쓸 조각만 꺼낸다 (전체 결과는 한 건에 수백 KB)."""
        sql = ("SELECT w.symbol, w.horizon, r.id, r.created_at, r.name, r.price, r.change, r.verdicts, r.modules, r.headline, r.action "
               "FROM stock.watchlist w LEFT JOIN LATERAL (SELECT id, created_at, name, price, result->'quote'->'change' AS change, "
               "result->'verdicts' AS verdicts, result->'modules' AS modules, result->'narrative'->>'headline' AS headline, "
               "result->'advice'->>'action' AS action FROM stock.analysis_run a "
               "WHERE a.symbol = w.symbol AND a.horizon = w.horizon ORDER BY created_at DESC LIMIT 1) r ON true "
               "ORDER BY w.added_at, w.symbol")
        with self.pool.connection() as c:
            rows = c.execute(sql).fetchall()
        out = []
        for sym, hz, rid, at, name, price, change, verdicts, modules, headline, action in rows:
            out.append({"symbol": sym, "horizon": hz, "run_id": rid and str(rid), "created_at": at, "name": name, "price": price, "change": change,
                        "headline": headline, "action": action,
                        "verdicts": {h: {k: v.get(k) for k in ("verdict", "label", "strong", "score")} for h, v in (verdicts or {}).items()},
                        "modules": [{k: m.get(k) for k in ("key", "label", "score", "confidence", "available")} for m in (modules or [])]})
        return out

    def add_watch(self, symbol: str, horizon: str) -> None:
        with self.pool.connection() as c:
            c.execute("INSERT INTO stock.watchlist (symbol, horizon) VALUES (%s, %s) "
                      "ON CONFLICT (symbol) DO UPDATE SET horizon = EXCLUDED.horizon", (symbol, horizon))

    def remove_watch(self, symbol: str) -> None:
        with self.pool.connection() as c:
            c.execute("DELETE FROM stock.watchlist WHERE symbol = %s", (symbol,))

    # --- 가중치 자동 조정 ---
    def weights_history(self, n: int = 6) -> list[dict]:
        with self.pool.connection() as c:
            rows = c.execute("SELECT id, created_at, weights, adopted, report FROM stock.engine_weights ORDER BY id DESC LIMIT %s",
                             (n,)).fetchall()
        return [dict(zip(("id", "created_at", "weights", "adopted", "report"), r)) for r in rows]

    def save_weights(self, weights: dict, adopted: bool, report: dict) -> int:
        with self.pool.connection() as c:
            return c.execute("INSERT INTO stock.engine_weights (weights, adopted, report) VALUES (%s, %s, %s) RETURNING id",
                             (Jsonb(weights), adopted, Jsonb(report))).fetchone()[0]

    def runs_for_tuning(self) -> list[dict]:
        """판단 기간(가장 짧은 30일)이 지난 실행: 모듈 점수와 기간별 유사 국면, 실행 가격."""
        with self.pool.connection() as c:
            rows = c.execute("SELECT symbol, created_at, price, result->'modules', result->'analogs' FROM stock.analysis_run "
                             "WHERE created_at < now() - interval '30 days'").fetchall()
        return [dict(zip(("symbol", "created_at", "price", "modules", "analogs"), r)) for r in rows]
