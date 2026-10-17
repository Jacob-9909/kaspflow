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
  GET /backtest?symbol=...&interval=5m&strategy=macd : 전략 백테스트 + 매수/매도 마커
  GET /backtest/strategies : 전략 목록/기본 파라미터
  GET /news?symbol=...     : 최근 뉴스 신호 (제목·링크·감정 확률)
  GET /news/health         : 마지막 수집 시각과 경과 시간 (수집 중단 감지)
  GET /news/pipeline       : Laya 흐름 (단계별 건수·분포·정확도 측정)
  GET /news/score?symbol=...&interval=15m : 봉별 news_score 선 (대시보드 보조 차트)
  GET /features?symbol=...&interval=15m   : 봉별 기술 지표 + 뉴스 피처 (JSON/CSV 내보내기)

[확인 방법]
  컨테이너가 뜨면 브라우저에서 http://localhost:8000/docs 접속
  -> FastAPI가 자동 생성한 대화형 API 문서(Swagger UI)에서 바로 테스트 가능
"""
import json
import os
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta

import pandas as pd
import psycopg
import psycopg.errors
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from fastapi import FastAPI, Query, HTTPException, Response

import backtest
import features
import news_features
import news_queries

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
    "30m": "30 minutes",
    "1h": "1 hour",
    "4h": "4 hours",
    "1d": "1 day",
}


@app.get("/intervals")
def list_intervals():
    """대시보드가 인터벌 버튼을 그릴 수 있게 지원 목록을 반환."""
    return {"intervals": list(INTERVALS.keys())}


def _query_ohlc(symbol: str, interval: str, limit: int, before: datetime | None = None) -> list[dict]:
    """특정 심볼의 최근 OHLC 봉을 시간 오름차순 dict 리스트로 반환. (/ohlc, /backtest 공용)

    before 를 주면 '그 봉 시작 시각보다 이전'의 봉만 대상으로 한다 (과거로 거슬러 올라가는 페이지네이션).
    before 가 속한 버킷은 포함하지 않는다. 즉 클라이언트가 가진 가장 오래된 봉의 window_start 를
    그대로 넘기면 그 봉 바로 앞의 limit 개를 받는다 (겹침/공백 없음).

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

    # before 는 값을 SQL 문자열에 끼우지 않고 파라미터로만 넘긴다 (절 자체만 조건부로 붙임).
    #   버킷 경계로 내림(date_bin)해서, 경계가 아닌 값이 와도 걸친 버킷을 반쪽만 반환하지 않는다.
    params: dict = {"iv": pg_interval, "sym": symbol, "lim": limit, "before": before}
    cut_plain = "AND window_start < date_bin(%(iv)s::interval, %(before)s::timestamptz, TIMESTAMPTZ 'epoch')" if before else ""
    cut_alias = "AND o.window_start < date_bin(%(iv)s::interval, %(before)s::timestamptz, TIMESTAMPTZ 'epoch')" if before else ""

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            if interval == "1m":
                # 원본 그대로 (재집계 불필요)
                cur.execute(
                    f"""
                    SELECT symbol, window_start, open, high, low, close, volume, trade_count
                    FROM (
                        SELECT * FROM ohlc_1m
                        WHERE symbol = %(sym)s {cut_plain}
                        ORDER BY window_start DESC
                        LIMIT %(lim)s
                    ) sub
                    ORDER BY window_start ASC
                    """,
                    params,
                )
            else:
                # 1분봉을 interval 버킷으로 재집계.
                #   date_bin('5 minutes', window_start, 'epoch') : UTC epoch(1970) 기준
                #
                # [성능] 예전에는 심볼의 '전체' 1분봉을 훑은 뒤 마지막 limit 개만 잘라 썼다.
                #   과거 데이터를 백필해 행이 수십만 개가 되면 요청마다(대시보드는 5초 폴링) 전부 읽게 된다.
                #   그래서 latest CTE 로 '가장 최근 버킷'을 먼저 찾고(인덱스 max 조회),
                #   거기서 (limit-1) 버킷만큼만 거슬러 올라간 구간(window_start >= 하한)만 집계한다.
                #   결과는 '최근 limit 개 버킷'으로 같다. (체결이 전혀 없는 긴 공백이 있으면 그만큼 적게 나온다)
                cur.execute(
                    f"""
                    WITH latest AS (
                        SELECT date_bin(%(iv)s::interval, max(window_start), TIMESTAMPTZ 'epoch') AS bucket
                        FROM ohlc_1m
                        WHERE symbol = %(sym)s {cut_plain}
                    ),
                    binned AS (
                        SELECT
                            date_bin(%(iv)s::interval, o.window_start, TIMESTAMPTZ 'epoch') AS bucket,
                            o.window_start, o.open, o.high, o.low, o.close, o.volume, o.trade_count
                        FROM ohlc_1m o, latest l
                        WHERE o.symbol = %(sym)s {cut_alias}
                          AND o.window_start >= l.bucket - (%(iv)s::interval * (%(lim)s::int - 1))
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
                    SELECT %(sym)s::text AS symbol, window_start, open, high, low, close, volume, trade_count
                    FROM (
                        SELECT * FROM agg ORDER BY window_start DESC LIMIT %(lim)s
                    ) sub
                    ORDER BY window_start ASC
                    """,
                    params,
                )
            rows = cur.fetchall()

    return rows


def _earliest_bucket(symbol: str, interval: str) -> datetime | None:
    """이 심볼에 저장된 가장 오래된 1분봉이 속한 interval 버킷의 시작 시각 (없으면 None).

    클라이언트가 '더 불러올 과거가 있는지'를 판단하는 기준이다. 봉 개수가 모자란 것만으로
    끝이라고 단정하면, 중간에 데이터 공백이 있을 때 그 앞의 과거를 영영 못 불러온다.
    """
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT date_bin(%s::interval, min(window_start), TIMESTAMPTZ 'epoch') FROM ohlc_1m WHERE symbol = %s",
            (INTERVALS[interval], symbol),
        ).fetchone()
    return row[0] if row else None


@app.get("/ohlc")
def get_ohlc(
    symbol: str = Query(..., description="심볼 (예: BTCUSDT)"),
    interval: str = Query("1m", description="봉 간격: 1m/5m/15m/1h/4h/1d"),
    limit: int = Query(120, ge=1, le=1000, description="최근 몇 개의 봉을 가져올지"),
    before: datetime | None = Query(
        None,
        description="이 시각(ISO8601)보다 이전의 봉만. 가진 가장 오래된 봉의 window_start 를 넘기면 그 앞의 limit 개를 받는다 (과거 페이지네이션).",
    ),
):
    """특정 심볼의 최근 OHLC 봉을 시간 오름차순으로 반환. (1m 외 간격은 재집계)"""
    rows = _query_ohlc(symbol, interval, limit, before)
    earliest = _earliest_bucket(symbol, interval)

    # datetime -> ISO 문자열로 직렬화
    for r in rows:
        r["window_start"] = r["window_start"].isoformat()

    return {
        "symbol": symbol,
        "interval": interval,
        "count": len(rows),
        "earliest": earliest.isoformat() if earliest else None,  # 저장된 가장 오래된 버킷 (과거 끝 판단용)
        "candles": rows,
    }


# ------------------------------------------------------------
# 백테스트 (midas-touch 전략 이식, 로직은 backtest.py)
# ------------------------------------------------------------
# 분 단위 길이: 샤프 비율 연환산에 사용
INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}

# 공개 엔드포인트라 연산량을 제한한다 (복합 전략은 하위 5개 전략을 돌린다).
BACKTEST_MAX_BARS = 5000
PARAMS_MAX_LEN = 500


@app.get("/backtest/strategies")
def list_backtest_strategies():
    """전략 목록과 기본 파라미터/리스크 설정. 대시보드가 입력 폼을 그리는 데 쓴다."""
    return {
        "strategies": [
            {"id": sid, "label": label, "params": backtest.DEFAULT_PARAMS[sid],
             "requires_news": sid == backtest.NEWS_STRATEGY}
            for sid, label in backtest.STRATEGY_LABELS.items()
        ],
        "risk_defaults": backtest.DEFAULT_RISK,
        "max_bars": BACKTEST_MAX_BARS,
    }


@app.get("/backtest")
def run_backtest_endpoint(
    symbol: str = Query(..., description="심볼 (예: BTCUSDT)"),
    interval: str = Query("5m", description="전략을 평가할 봉 간격: 1m/5m/15m/30m/1h/4h/1d"),
    strategy: str = Query("sma_crossover", description="sma_crossover/macd/rsi/bollinger/obv/combined/news_sentiment"),
    limit: int = Query(500, ge=10, le=BACKTEST_MAX_BARS, description="백테스트에 쓸 최근 봉 개수"),
    initial_capital: float = Query(10000, gt=0, le=1e12, description="초기 자본 (USDT)"),
    fee_bps: float = Query(10, ge=0, le=100, description="편도 수수료 (bp, 10 = 0.10%)"),
    stop_loss_pct: float | None = Query(None, gt=0, lt=1, description="진입가 대비 손절 비율 (0.02 = 2%)"),
    take_profit_pct: float | None = Query(None, gt=0, lt=1, description="진입가 대비 익절 비율"),
    trailing_stop_pct: float | None = Query(None, gt=0, lt=1, description="보유 중 고점 대비 추격 손절 비율"),
    params: str | None = Query(None, max_length=PARAMS_MAX_LEN, description='전략 파라미터 JSON (예: {"short_window":5,"long_window":20})'),
    fill: str | None = Query(None, description="체결 가격: close(신호 봉 종가) / next_open(다음 봉 시가, 누수 방지). 기본: news_sentiment 는 next_open, 나머지는 close"),
    compare: bool = Query(False, description="true 면 같은 기간·비용의 복합 전략·단순 보유 성과를 baselines 로 함께 반환"),
    model_version: str | None = Query(None, description="news_sentiment 가 쓸 뉴스 모델 버전 (기본: 가장 최근에 쓰인 버전)"),
):
    """선택한 봉 간격·전략으로 백테스트를 돌려 성과, 거래 목록, 차트용 매수/매도 마커를 반환.

    - 봉은 /ohlc 와 같은 규칙으로 가져오므로 같은 interval/limit 이면 차트의 봉과 시각이 일치한다.
    - 아직 집계 중인 마지막 봉은 시뮬레이션에서 제외한다 (신호 리페인팅 방지).
    - 체결은 기본적으로 신호 봉의 종가 기준이며(fill=close) 슬리피지는 반영하지 않는다.
      fill=next_open 이면 신호 봉 다음 봉의 시가로 체결한다. (news_sentiment 의 기본값)
    - news_sentiment 일 때만 뉴스 신호를 조회한다 (뉴스 테이블이 없어도 다른 전략은 영향받지 않는다).
    """
    if fill is None:
        fill = "next_open" if strategy == backtest.NEWS_STRATEGY else "close"
    if fill not in backtest.FILL_MODES:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 fill: {fill}")
    if interval not in INTERVALS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 interval: {interval}")
    if strategy not in backtest.STRATEGY_LABELS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 strategy: {strategy}")

    overrides = _parse_params(params)

    candles = _query_ohlc(symbol, interval, limit)
    news = None
    news_mv = None
    if strategy == backtest.NEWS_STRATEGY:
        try:
            lookback = backtest.merge_params(strategy, overrides)["lookback_min"]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        news, news_mv = _load_news(symbol, candles, lookback, model_version)
    try:
        result = backtest.run_backtest(
            candles,
            strategy=strategy,
            params=overrides,
            initial_capital=initial_capital,
            risk={
                "fee_bps": fee_bps,
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "trailing_stop_pct": trailing_stop_pct,
            },
            interval_minutes=INTERVAL_MINUTES[interval],
            fill=fill,
            news=news,
            compare=compare,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if "news" in result:
        result["news"]["model_version"] = news_mv

    return {"symbol": symbol, "interval": interval, **result}


# ------------------------------------------------------------
# 뉴스 (docs/news-signal-design.md 6.4절)
#   news_raw / news_signal 은 db/news.sql 로 따로 만든다. 테이블이 없으면 이 엔드포인트들만 503 을 내고
#   시세·기존 백테스트는 영향받지 않는다.
# ------------------------------------------------------------
def _parse_params(params: str | None) -> dict | None:
    if not params:
        return None
    try:
        overrides = json.loads(params)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="params 는 JSON 객체여야 합니다")
    if not isinstance(overrides, dict):
        raise HTTPException(status_code=400, detail="params 는 JSON 객체여야 합니다")
    return overrides


@contextmanager
def _news_conn():
    """뉴스 테이블 조회용 연결. 테이블이 없으면 503(적용 안내)로 바꿔 준다."""
    try:
        with pool.connection() as conn:
            yield conn
    except psycopg.errors.UndefinedTable:
        raise HTTPException(status_code=503, detail="뉴스 테이블이 없습니다. db/news.sql 을 먼저 적용하세요")


def _load_news(symbol: str, candles: list[dict], lookback_min: int, model_version: str | None):
    """백테스트/피처 계산에 쓸 뉴스 신호 (이 종목 + MARKET). 반환: (신호 목록, 사용한 model_version)."""
    if not candles:
        return [], None
    since = candles[0]["window_start"] - timedelta(minutes=lookback_min)
    with _news_conn() as conn:
        mv = model_version or news_queries.latest_model_version(conn)
        return news_queries.fetch_signals(conn, symbol, since, mv), mv


@app.get("/news")
def list_news(
    symbol: str | None = Query(None, description="심볼 (예: BTCUSDT, 시장 전체는 MARKET). 비우면 전체"),
    limit: int = Query(50, ge=1, le=200),
    since: datetime | None = Query(None, description="이 시각(ISO8601) 이후 수집된 신호만"),
    model_version: str | None = Query(None, description="기본: 가장 최근에 쓰인 모델 버전"),
):
    """최근 뉴스 신호(최신순): 제목·링크·소스와 관련도·감정 확률·이벤트 유형."""
    with _news_conn() as conn:
        rows = news_queries.recent_news(conn, symbol, limit, since, model_version)
    return {"symbol": symbol, "count": len(rows), "news": rows}


@app.get("/news/health")
def news_health():
    """뉴스 수집 상태: 마지막 collected_at 과 경과 시간. status=stale 이면 수집 중단을 의심한다."""
    with _news_conn() as conn:
        return news_queries.health(conn)


@app.get("/news/pipeline")
def news_pipeline(hours: int = Query(168, ge=1, le=24 * 90, description="최근 몇 시간을 집계할지 (기본 7일)")):
    """Laya 흐름 패널: 수집 -> 매칭 -> 분류 단계별 건수, 감정·이벤트 분포, 가장 최근 정확도 측정, 전략 기본값."""
    with _news_conn() as conn:
        out = news_queries.pipeline(conn, hours)
    out["strategy_defaults"] = news_features.NEWS_DEFAULT_PARAMS
    return out


def _bars_with_news(symbol, interval, limit, params, model_version):
    """차트와 같은 규칙으로 봉을 가져오고 봉별 뉴스 피처를 계산한다. 반환: (봉, 피처, 사용 파라미터, 모델 버전)."""
    if interval not in INTERVALS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 interval: {interval}")
    try:
        p = news_features.validate_news_params(_parse_params(params))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    candles = _query_ohlc(symbol, interval, limit)
    news, mv = _load_news(symbol, candles, p["lookback_min"], model_version)
    times = [backtest._to_epoch(c["window_start"]) for c in candles]
    feats = news_features.news_features(times, INTERVAL_MINUTES[interval], news, p)
    return candles, times, feats, p, mv


@app.get("/news/score")
def news_score_series(
    symbol: str = Query(..., description="심볼 (예: BTCUSDT)"),
    interval: str = Query("15m", description="봉 간격: 1m/5m/15m/30m/1h/4h/1d"),
    limit: int = Query(500, ge=10, le=BACKTEST_MAX_BARS),
    params: str | None = Query(None, max_length=PARAMS_MAX_LEN, description="news_sentiment 와 같은 피처 파라미터 JSON"),
    model_version: str | None = Query(None),
):
    """봉별 news_score / news_weight / news_count (차트 아래 보조 선). 마지막 진행 중인 봉도 포함한다."""
    candles, times, feats, p, mv = _bars_with_news(symbol, interval, limit, params, model_version)
    series = [
        {"time": t, "score": round(float(feats["score"][i]), 6), "weight": round(float(feats["weight"][i]), 6),
         "count": int(feats["count"][i])}
        for i, t in enumerate(times)
    ]
    return {"symbol": symbol, "interval": interval, "model_version": mv, "params_used": p, "series": series}


@app.get("/features")
def feature_table(
    symbol: str = Query(..., description="심볼 (예: BTCUSDT)"),
    interval: str = Query("15m", description="봉 간격: 1m/5m/15m/30m/1h/4h/1d"),
    limit: int = Query(500, ge=10, le=BACKTEST_MAX_BARS),
    params: str | None = Query(None, max_length=PARAMS_MAX_LEN, description="뉴스 피처 파라미터 JSON"),
    model_version: str | None = Query(None),
    format: str = Query("json", pattern="^(json|csv)$", description="json | csv (오프라인 ML 학습용 내보내기)"),
):
    """봉별 OHLCV + 기술 지표 + 뉴스 피처. 라벨은 없다 (features.py 의 주의 참고)."""
    candles, _times, feats, p, mv = _bars_with_news(symbol, interval, limit, params, model_version)
    rows = features.build_feature_rows(candles, feats)
    if format == "csv":
        csv = pd.DataFrame(rows).to_csv(index=False)
        return Response(content=csv, media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{symbol}_{interval}_features.csv"'})
    return {"symbol": symbol, "interval": interval, "model_version": mv, "params_used": p, "rows": rows}
