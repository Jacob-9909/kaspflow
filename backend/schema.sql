-- kaspflow 주식 검증기 스키마. 백엔드가 기동할 때마다 실행한다 (전부 IF NOT EXISTS 라 몇 번 돌려도 같다).
-- 위치: oracle_vm 호스트 PostgreSQL 17 의 crypto DB (1세대 코인 테이블 ohlc_1m, news_* 는 public 에 그대로 둔다).
CREATE SCHEMA IF NOT EXISTS stock;

-- 외부 데이터(yfinance, 뉴스, LLM 응답) 캐시. key 앞부분이 종류다: yf:prices:AAPL, news:AAPL, llm:<hash> ...
CREATE TABLE IF NOT EXISTS stock.cache (
    key         TEXT        PRIMARY KEY,
    payload     JSONB       NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_cache_fetched ON stock.cache (fetched_at);

-- 검증·시뮬레이션 실행 기록. result 에 화면을 다시 그릴 수 있는 전체 결과가 들어 있다.
--   price   : 실행 시점 가격 (판단 뒤 수익률 = 적중률 계산의 기준)
--   verdict : 선택한 기간(horizon)의 판단 buy | hold | sell
CREATE TABLE IF NOT EXISTS stock.analysis_run (
    id          UUID        PRIMARY KEY,
    symbol      TEXT        NOT NULL,
    name        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    horizon     TEXT        NOT NULL CHECK (horizon IN ('short', 'mid', 'long')),
    verdict     TEXT        NOT NULL CHECK (verdict IN ('buy', 'hold', 'sell')),
    score       DOUBLE PRECISION NOT NULL,
    confidence  DOUBLE PRECISION NOT NULL,
    price       DOUBLE PRECISION NOT NULL,
    duration_ms INTEGER,
    result      JSONB       NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_symbol_time ON stock.analysis_run (symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_time ON stock.analysis_run (created_at DESC);

-- 관심 종목. 평일 미국 장 마감 뒤 하루 한 번 자동으로 판단한다 (기록이 쌓여 적중률의 실전 표본이 된다).
CREATE TABLE IF NOT EXISTS stock.watchlist (
    symbol    TEXT        PRIMARY KEY,
    horizon   TEXT        NOT NULL DEFAULT 'mid' CHECK (horizon IN ('short', 'mid', 'long')),
    added_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 판단 엔진 가중치 자동 조정 기록 (tuning.py, 주 1회). 가장 최근 행의 weights 를 판단에 쓴다 (없으면 코드의 기본값).
--   adopted : 한 기간이라도 기본값 대신 조정한 가중치를 쓰는가.  report : 기간별 IC, 표본 수, 표본 밖 검증, 이유
CREATE TABLE IF NOT EXISTS stock.engine_weights (
    id          SERIAL      PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    weights     JSONB       NOT NULL,
    adopted     BOOLEAN     NOT NULL,
    report      JSONB       NOT NULL
);
