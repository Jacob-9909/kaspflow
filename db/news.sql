-- ============================================================
-- 뉴스·감정 시그널 스키마 (PostgreSQL)
-- ------------------------------------------------------------
-- 설계: docs/news-signal-design.md 5절.
--
-- 적용 (init.sql 과 달리 자동 실행되지 않는다. 여러 번 실행해도 안전 = 멱등):
--   VM (네이티브 PG17):  sudo -u postgres psql -d crypto -f db/news.sql
--   로컬 compose:        docker compose exec -T postgres psql -U crypto -d crypto < db/news.sql
--
-- 배포 순서: backend / news-collector / signal-worker 를 올리기 '전에' 먼저 적용한다.
-- 시각은 전부 UTC(timestamptz). 표시할 때만 KST 로 바꾼다.
-- ============================================================

-- ------------------------------------------------------------
-- 기사 원문. signal-worker 가 ON CONFLICT DO NOTHING 으로 넣는다.
--   collected_at : 수집기가 이 기사를 '처음' 받은 시각. 백테스트 기준 시각이다.
--                  (published_at 이 아니라 시스템이 실제로 알게 된 시각을 쓴다)
--   published_at : 기사 발행 시각. RSS 에 없으면 NULL. 오래된 기사 제외(max_age_min)에 쓴다.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS news_raw (
    article_id    TEXT        PRIMARY KEY,          -- 정규화한 URL 의 SHA-256 (hex)
    source        TEXT        NOT NULL,
    url           TEXT        NOT NULL,
    title         TEXT        NOT NULL,
    body          TEXT,
    lang          TEXT,
    published_at  TIMESTAMPTZ,
    collected_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_raw_collected ON news_raw (collected_at DESC);

-- ------------------------------------------------------------
-- 모델 결과. 기사 x 종목 x 모델 버전 단위. 모델/질문을 바꿔도 원문은 그대로 두고 신호만 다시 계산한다.
--   symbol        : BTCUSDT ... 또는 가상 심볼 MARKET (시장 전체 기사)
--   model_version : 예) laya-en@0.3.20/schema1  (질문 문구를 바꾸면 schema 번호를 올린다)
--   collected_at  : news_raw.collected_at 의 복사본 (조인용). 메시지 값이 아니라 항상 news_raw 의 최초 값.
--   p_relevant    : relevance = 'mainly about it' 확률
--   p_pos/neu/neg : sentiment 확률
--   event_type    : event_type 의 top-1 라벨 (영어 키, 예: regulation_legal)
--   p_event       : event_type 전체 확률 분포 {"listing_delisting":0.1, ...} (보정·재분석용)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS news_signal (
    article_id     TEXT        NOT NULL REFERENCES news_raw (article_id),
    symbol         TEXT        NOT NULL,
    model_version  TEXT        NOT NULL,
    collected_at   TIMESTAMPTZ NOT NULL,
    p_relevant     DOUBLE PRECISION,
    p_pos          DOUBLE PRECISION,
    p_neu          DOUBLE PRECISION,
    p_neg          DOUBLE PRECISION,
    event_type     TEXT,
    p_event        JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (article_id, symbol, model_version)
);
CREATE INDEX IF NOT EXISTS idx_news_signal_symbol_time
    ON news_signal (symbol, collected_at DESC);

-- ------------------------------------------------------------
-- 사람이 직접 붙인 정답. Laya 정확도 측정과 이후 파인튜닝 데이터로 쓴다.
--   field : relevance | sentiment | event_type
--   label : 질문 스키마의 선택지 의미(영어 키)와 같은 값
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS news_label (
    article_id  TEXT        NOT NULL REFERENCES news_raw (article_id),
    symbol      TEXT        NOT NULL,
    field       TEXT        NOT NULL,
    label       TEXT        NOT NULL,
    labeled_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (article_id, symbol, field)
);
