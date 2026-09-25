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


# 지원하는 인터벌 -> PostgreSQL interval 문자열 매핑.
# 1m 은 원본 그대로, 나머지는 1분봉을 date_bin 으로 묶어 재집계한다.
INTERVALS = {
    "1m": "1 minute",
    "5m": "5 minutes",
    "15m": "15 minutes",
    "1h": "1 hour",
    "4h": "4 hours",
    "1d": "1 day",
}


@app.get("/intervals")
def list_intervals():
    """대시보드가 인터벌 버튼을 그릴 수 있게 지원 목록을 반환."""
    return {"intervals": list(INTERVALS.keys())}


@app.get("/ohlc")
def get_ohlc(
    symbol: str = Query(..., description="심볼 (예: BTCUSDT)"),
    interval: str = Query("1m", description="봉 간격: 1m/5m/15m/1h/4h/1d"),
    limit: int = Query(120, ge=1, le=1000, description="최근 몇 개의 봉을 가져올지"),
):
    """특정 심볼의 최근 OHLC 봉을 시간 오름차순으로 반환.

    저장소에는 1분봉(ohlc_1m)만 있으므로, 1m 이 아니면 여기서 **재집계**한다.
      - date_bin(interval, window_start, 기준시각) 으로 1분봉을 버킷으로 묶고
      - OHLC 규칙대로 합친다:
          open  = 버킷에서 가장 이른 봉의 open  (window_start ASC first)
          close = 버킷에서 가장 늦은 봉의 close (window_start DESC first)
          high  = max(high), low = min(low)
          volume = sum(volume), trade_count = sum(trade_count)
    """
    if interval not in INTERVALS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 interval: {interval}")

    pg_interval = INTERVALS[interval]

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            if interval == "1m":
                # 원본 그대로 (재집계 불필요)
                cur.execute(
                    """
                    SELECT symbol, window_start, open, high, low, close, volume, trade_count
                    FROM (
                        SELECT * FROM ohlc_1m
                        WHERE symbol = %s
                        ORDER BY window_start DESC
                        LIMIT %s
                    ) sub
                    ORDER BY window_start ASC
                    """,
                    (symbol, limit),
                )
            else:
                # 1분봉을 interval 버킷으로 재집계.
                #   date_bin('5 minutes', window_start, 'epoch') : UTC epoch(1970) 기준
                #   정렬된 버킷마다 first/last 를 쓰기 위해 DISTINCT ON + 서브쿼리 대신
                #   윈도 없이 집계 + 상관 서브쿼리로 open/close 를 뽑는다.
                cur.execute(
                    """
                    WITH binned AS (
                        SELECT
                            date_bin(%s::interval, window_start, TIMESTAMPTZ 'epoch') AS bucket,
                            window_start, open, high, low, close, volume, trade_count
                        FROM ohlc_1m
                        WHERE symbol = %s
                    ),
                    agg AS (
                        SELECT
                            bucket AS window_start,
                            max(high)  AS high,
                            min(low)   AS low,
                            sum(volume) AS volume,
                            sum(trade_count) AS trade_count,
                            -- open: 버킷 내 가장 이른 봉의 open
                            (array_agg(open ORDER BY window_start ASC))[1]  AS open,
                            -- close: 버킷 내 가장 늦은 봉의 close
                            (array_agg(close ORDER BY window_start DESC))[1] AS close
                        FROM binned
                        GROUP BY bucket
                    )
                    SELECT %s AS symbol, window_start, open, high, low, close, volume, trade_count
                    FROM (
                        SELECT * FROM agg ORDER BY window_start DESC LIMIT %s
                    ) sub
                    ORDER BY window_start ASC
                    """,
                    (pg_interval, symbol, symbol, limit),
                )
            rows = cur.fetchall()

    # datetime -> ISO 문자열로 직렬화
    for r in rows:
        r["window_start"] = r["window_start"].isoformat()

    return {"symbol": symbol, "interval": interval, "count": len(rows), "candles": rows}
