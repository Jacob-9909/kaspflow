"""
Backend API (FastAPI): PostgreSQL 조회 -> JSON 제공
============================================================
[이 파일이 하는 일]
  Spark가 저장한 집계 결과(ohlc_1m)를 읽어서 대시보드가 쓰기 좋은
  JSON 형태로 돌려주는 REST API.

[데이터 흐름에서의 위치]
  ... -> Spark -> Postgres -> [이 API] -> 대시보드
                               ^^^^^^^

[엔드포인트]
  GET /health          : 헬스체크 (DB 연결 확인)
  GET /symbols         : 저장된 심볼 목록
  GET /ohlc?symbol=... : 특정 심볼의 1분봉 최근 N개

[확인 방법]
  컨테이너가 뜨면 브라우저에서 http://localhost:8000/docs 접속
  -> FastAPI가 자동 생성한 대화형 API 문서(Swagger UI)에서 바로 테스트 가능
"""
import os
from contextlib import asynccontextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from fastapi import FastAPI, Query, HTTPException

# ------------------------------------------------------------
# DB 접속 정보 (docker-compose environment 로 주입)
# ------------------------------------------------------------
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "crypto")
POSTGRES_USER = os.getenv("POSTGRES_USER", "crypto")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "crypto")

CONNINFO = (
    f"host={POSTGRES_HOST} port={POSTGRES_PORT} dbname={POSTGRES_DB} "
    f"user={POSTGRES_USER} password={POSTGRES_PASSWORD}"
)

# 커넥션 풀: 요청마다 새 연결을 만들지 않고 재사용 -> 빠르고 안정적
# open=False 로 만들고 lifespan에서 명시적으로 open (psycopg 권장 방식)
pool = ConnectionPool(CONNINFO, min_size=1, max_size=5, open=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작 시 풀을 열고, 종료 시 닫는다."""
    pool.open()
    yield
    pool.close()


app = FastAPI(title="Crypto Realtime Dashboard API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    """DB에 SELECT 1 을 날려 연결이 살아있는지 확인."""
    try:
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}
    except Exception as e:
        # DB가 아직 안 떴거나 연결 실패
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")


@app.get("/symbols")
def list_symbols():
    """ohlc_1m 테이블에 데이터가 있는 심볼 목록을 반환."""
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM ohlc_1m ORDER BY symbol"
        ).fetchall()
    # rows는 [(symbol,), ...] 형태 -> 평평한 리스트로
    return {"symbols": [r[0] for r in rows]}


@app.get("/ohlc")
def get_ohlc(
    symbol: str = Query(..., description="심볼 (예: BTCUSDT)"),
    limit: int = Query(60, ge=1, le=1000, description="최근 몇 개의 1분봉을 가져올지"),
):
    """특정 심볼의 최근 1분봉을 시간 오름차순으로 반환.

    대시보드가 차트를 그리기 좋게, 최신 limit개를 뽑되
    시간순(오래된 -> 최신)으로 정렬해서 준다.
    """
    with pool.connection() as conn:
        # dict_row: 결과를 {컬럼명: 값} 딕셔너리로 받는다 (JSON 변환 편함)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT symbol, window_start, open, high, low, close, volume, trade_count
                FROM (
                    SELECT *
                    FROM ohlc_1m
                    WHERE symbol = %s
                    ORDER BY window_start DESC
                    LIMIT %s
                ) sub
                ORDER BY window_start ASC
                """,
                (symbol, limit),
            )
            rows = cur.fetchall()

    # datetime -> ISO 문자열로 직렬화
    for r in rows:
        r["window_start"] = r["window_start"].isoformat()

    return {"symbol": symbol, "count": len(rows), "candles": rows}
