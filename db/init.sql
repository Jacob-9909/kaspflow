-- ============================================================
-- DB 스키마 초기화 (PostgreSQL)
-- ------------------------------------------------------------
-- 이 파일은 postgres 컨테이너가 "최초 기동"될 때 한 번 자동 실행된다.
-- (docker-compose.yml 에서 /docker-entrypoint-initdb.d 로 마운트)
--
-- 주의: 이미 pgdata 볼륨에 데이터가 있으면 재실행되지 않는다.
--       스키마를 바꾸고 다시 적용하려면:  docker compose down -v  (볼륨 삭제)
-- ============================================================

-- ------------------------------------------------------------
-- 1분봉(OHLC) 테이블  (설계서 3.2 절)
--   Spark 잡이 (symbol, window_start) 기준으로 upsert 한다.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ohlc_1m (
    symbol        TEXT             NOT NULL,           -- 심볼 (예: BTCUSDT)
    window_start  TIMESTAMPTZ      NOT NULL,           -- 1분 윈도우 시작 시각
    open          DOUBLE PRECISION,                    -- 시가
    high          DOUBLE PRECISION,                    -- 고가
    low           DOUBLE PRECISION,                    -- 저가
    close         DOUBLE PRECISION,                    -- 종가
    volume        DOUBLE PRECISION,                    -- 1분 거래량
    trade_count   INTEGER,                             -- 체결 건수
    -- 같은 심볼의 같은 분(minute)은 한 행만 존재 -> upsert의 충돌 기준(PK)
    PRIMARY KEY (symbol, window_start)
);

-- 최신 데이터 조회(대시보드/API)를 빠르게 하기 위한 인덱스
CREATE INDEX IF NOT EXISTS idx_ohlc_symbol_time
    ON ohlc_1m (symbol, window_start DESC);

-- ------------------------------------------------------------
-- 급증 알림 테이블  (설계서 3.2 절, M6에서 채워짐)
--   지금은 스키마만 준비. 거래량/가격 급증 탐지 로직은 후속 단계.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS price_alert (
    id         BIGSERIAL PRIMARY KEY,
    symbol     TEXT        NOT NULL,
    ts         TIMESTAMPTZ NOT NULL,
    alert_type TEXT        NOT NULL,   -- 'VOLUME_SPIKE' | 'PRICE_JUMP'
    detail     TEXT
);

CREATE INDEX IF NOT EXISTS idx_alert_symbol_time
    ON price_alert (symbol, ts DESC);
