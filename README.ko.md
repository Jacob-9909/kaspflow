# kaspflow

[English](README.md) | **한국어**

[![Backend CI](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-backend.yml/badge.svg)](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-backend.yml)
[![Frontend CI](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-frontend.yml/badge.svg)](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-frontend.yml)

미국 주식의 매수·관망·매도를 판단하는 검증기입니다. 종목을 고르고 버튼을 누르면 기술적 분석, 통계 검증을 거친 전략 백테스트, 몬테카를로 시뮬레이션, 가치평가, 뉴스 감성, 투자 대가 6인의 체크리스트를 한 번에 돌려 기간별(1개월, 3개월, 1년) 판단을 냅니다.

- 데모: https://bit-kaspflow.duckdns.org (결과 보기는 공개, 실행은 비밀번호 필요)

```
Yahoo Finance ─┐                                   ┌─ 기간별 판단 (계량 엔진, 재현 가능)
Google News  ──┼─▶ FastAPI 파이프라인 (8단계) ─────┤
NVIDIA NIM   ──┘     PostgreSQL 캐시 + 실행 기록    └─ React 대시보드 (SSE 진행, 차트)
```

## 무엇을 검증하나

- **검증된 전략 신호**: 전략 11개(midas-touch 이식 6개 + 터틀 돌파, 시계열 모멘텀, 10개월 추세 필터, RSI(2), 변동성 돌파), 파라미터 변형 30개. 워크포워드 표본 밖 성과, 타이밍 실력을 보는 몬테카를로 순열 검정, DSR(다중 검정 보정 샤프). 통과한 전략만 투표합니다.
- **시뮬레이션**: CAPM 드리프트를 쓴 블록 부트스트랩 가격 경로 2,000개, 손절·목표 도달 확률, 비중, 과거 유사 국면.
- **가치평가·재무 품질**: FCFF DCF와 WACC x 영구성장률 민감도, 역DCF, PEG, 그레이엄, EPV, 피오트로스키 F, 알트만 Z, 애널리스트 컨센서스와 EPS 추정치 변화.
- **뉴스·투자 대가**: NIM이 최근 기사 제목을 분류하고, 버핏·그레이엄·린치·멍거·캐시 우드·드러켄밀러 체크리스트에 LLM 의견을 더합니다.
- **사후 검증**: 모든 실행을 저장하고 판단 기간이 지나면 적중 여부를 매깁니다.

판단은 코드가 계산하고 재현됩니다. LLM은 이유를 설명하지만 판단을 바꾸지 못합니다. 자세한 내용은 [docs/methodology.md](docs/methodology.md).

> 투자 권유가 아닙니다. 백테스트와 시뮬레이션은 미래 수익을 보장하지 않습니다.

## 기술 스택

- 백엔드: Python 3.12, FastAPI, pandas/numpy, yfinance, psycopg 3, PostgreSQL 17
- LLM: NVIDIA NIM (OpenAI 호환, JSON 구조화 출력)
- 프론트엔드: React, Vite, TypeScript, TradingView lightweight-charts, Pretendard
- 인프라: Docker Compose, GitHub Actions, GHCR (amd64 + arm64), Oracle Cloud ARM VM, Caddy

## 실행

```bash
cp .env.example .env          # NVIDIA_API_KEY(선택), APP_PASSWORD 채우기
docker compose up --build     # http://localhost:8501
```

테스트: `cd backend && python -m unittest discover -s tests` (`TEST_DATABASE_URL` 을 주면 PostgreSQL 테스트도 돕니다).

## 문서

- [아키텍처](docs/architecture.md) · [검증 방법론](docs/methodology.md) · [oracle_vm 런북](infra/vm-setup.md)
