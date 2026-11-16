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
