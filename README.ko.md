# kaspflow

[English](README.md) | **한국어**

[![Backend CI](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-backend.yml/badge.svg)](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-backend.yml)
[![Frontend CI](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-frontend.yml/badge.svg)](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-frontend.yml)

Binance 체결 스트림을 Kafka와 Spark Structured Streaming으로 1분봉 집계해 실시간 캔들 차트로 보여주는 데이터 파이프라인입니다.

- 데모: https://bit-kaspflow.duckdns.org

```
Binance WS ─▶ Producer ─▶ Kafka ─▶ Spark Streaming ─▶ PostgreSQL ─▶ FastAPI ─▶ React
                                   (1분봉 OHLC 집계)
```

## 주요 기능

- **실시간 수집과 집계**: 심볼을 파티션 키로 써서 순서를 보장하고, 워터마크와 체크포인트로 지연 이벤트와 재시작을 처리합니다. 결과는 `(symbol, minute)` 기준 upsert로 저장합니다.
- **캔들 차트**: `lightweight-charts`로 1분~1일 간격 캔들을 그립니다.
- **백테스트**: SMA, MACD, RSI, 볼린저 밴드, OBV 전략 신호를 차트에 표시하고 수익률과 거래 내역을 계산합니다.
- **뉴스 시그널 (선택)**: 코인 매체 RSS를 수집해 종목별 감정 점수로 바꾸고 백테스트 입력으로 씁니다. [설계 문서](docs/news-signal-design.md)

> 백테스트 결과는 슬리피지를 반영하지 않으며 투자 조언이 아닙니다.

## 기술 스택

- Streaming: Apache Kafka, Spark 3.5 Structured Streaming
- Backend: Python, FastAPI, PostgreSQL
- Frontend: React, Vite, TypeScript, lightweight-charts
- Infra: Docker Compose, GitHub Actions, GHCR, Oracle Cloud VM, Caddy

## 시작하기

Docker와 Docker Compose가 필요합니다.

```bash
cp .env.example .env
docker compose up --build
```

1~2분 뒤 다음 주소에서 확인할 수 있습니다. Spark 첫 실행은 커넥터를 내려받느라 더 걸립니다.

| 서비스 | 주소 |
|---|---|
| Dashboard | http://localhost:8501 |
| API docs | http://localhost:8000/docs |
| Kafka UI | http://localhost:8080 |

수집 심볼은 `.env`의 `SYMBOLS`로 바꿉니다. 종료는 `docker compose down`, DB까지 초기화하려면 `docker compose down -v`입니다.

## 프로젝트 구조

```
producer/         Binance WebSocket → Kafka
spark/            Kafka → 1분봉 집계 → PostgreSQL
db/               테이블 스키마
backend/          REST API, 백테스트 엔진
dashboard/        React 대시보드
news-collector/   RSS → Kafka
signal-worker/    뉴스 종목 매칭과 감정 분류
infra/            Caddy, 배포 스크립트
```

처음 읽는다면 `docker-compose.yml` → `producer/main.py` → `spark/jobs/ohlc_stream.py` 순서를 추천합니다.

## 테스트

```bash
cd backend && python -m unittest discover -s tests
```

## 문서

- [설계와 의사결정](docs/design.md)
- [아키텍처](docs/architecture.md)
- [VM 배포](infra/vm-setup.md)
