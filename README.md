# 실시간 암호화폐 시세 대시보드

거래소(Binance) WebSocket 체결 스트림을 **Kafka → Spark Structured Streaming → PostgreSQL → FastAPI → React(Vite)** 로 흘려보내는 엔드투엔드 실시간 데이터 파이프라인 학습 프로젝트입니다. 대시보드는 TradingView의 `lightweight-charts` 로 캔들차트를 그립니다.

설계 배경과 의사결정은 상위 폴더의 `crypto-realtime-dashboard-DESIGN.md` 를 참고하세요.

---

## 🗺️ 어디서부터 봐야 하나요? (읽는 순서)

데이터가 흐르는 순서대로 코드를 따라가면 파이프라인이 한눈에 이해됩니다.
각 파일 맨 위에는 "이 파일이 하는 일"과 "읽는 순서 추천" 주석이 있습니다.

| 순서 | 파일 | 무슨 일을 하나 | 핵심 개념 |
| --- | --- | --- | --- |
| 0️⃣ | `docker-compose.yml` | 전체 서비스가 어떻게 연결되는지 지도 | 서비스 의존성, 포트, 네트워크 |
| 1️⃣ | `producer/main.py` | 거래소 WebSocket → Kafka **produce** | 생산자(Producer), 토픽, 파티션 키 |
| 2️⃣ | `spark/jobs/ohlc_stream.py` | Kafka **구독** → 1분봉 집계 → DB 저장 | 소비자(Consumer), 윈도우, 워터마크, 체크포인트 |
| 3️⃣ | `db/init.sql` | 집계 결과가 저장되는 테이블 구조 | 스키마, upsert 충돌 키 |
| 4️⃣ | `backend/app.py` | DB 조회 → JSON API | REST, 커넥션 풀 |
| 5️⃣ | `dashboard/src/App.tsx` | API 호출 → 실시간 캔들차트 | 시각화, 폴링, React 상태 |

> 💡 **처음이라면**: `docker-compose.yml`(지도) → `producer`(입구) → `spark`(핵심) 순서만 봐도 Kafka와 Spark의 생산자–소비자 관계가 잡힙니다.

---

## 🏗️ 아키텍처

```
거래소 WS ──> Producer ──> Kafka ──> Spark Streaming ──> PostgreSQL ──> FastAPI ──> React(Vite)
 (Binance)   (produce)  (crypto-   (1분봉 OHLC 집계)     (저장)        (조회 API)   (차트/nginx)
                         trades)
                            │
                            └─(모니터링)─> Kafka UI
```

| 서비스 | 포트 (호스트) | 접속 |
| --- | --- | --- |
| Kafka UI | 8080 | http://localhost:8080 |
| Backend API (문서) | 8000 | http://localhost:8000/docs |
| Dashboard | 8501 | http://localhost:8501 |
| Kafka (외부 접속용) | 19092 | `localhost:19092` |
| PostgreSQL | 5432 | `localhost:5432` (user/pw/db = crypto) |

---

## 🚀 실행 방법

### 사전 준비
- Docker Desktop (또는 Docker Engine) + `docker compose`
- 인터넷 연결 (거래소 WebSocket + Spark 커넥터 최초 다운로드)

### 1. 환경 변수 파일 준비
```bash
cp .env.example .env
```
기본값 그대로도 동작합니다. 수집 심볼을 바꾸려면 `.env` 의 `SYMBOLS` 를 수정하세요.

### 2. 단계별로 띄워보기 (권장 — 학습용)

**M1. 인프라만 먼저 확인**
```bash
docker compose up kafka kafka-ui
```
→ http://localhost:8080 (Kafka UI)가 뜨면 성공.

**M2. 수집 추가 — Producer가 메시지를 넣는지 확인**
```bash
docker compose up -d kafka kafka-ui
docker compose up producer
```
→ Kafka UI의 Topics → `crypto-trades` 에서 실시간 메시지가 쌓이는지 확인.

**M3~M5. 전체 파이프라인 기동**
```bash
docker compose up --build
```
→ 잠시(1~2분) 기다린 뒤:
- Dashboard: http://localhost:8501
- API 문서: http://localhost:8000/docs

> ⏳ **Spark 최초 실행은 느립니다**: `--packages` 로 Kafka 커넥터/JDBC 드라이버를 처음 내려받기 때문입니다. 로그에 `스트리밍 시작` 이 뜨면 정상입니다.

### 3. 종료
```bash
docker compose down        # 컨테이너만 정리 (DB 데이터 유지)
docker compose down -v      # 볼륨까지 삭제 (DB 초기화 — 스키마 바꿨을 때)
```

### 4. (선택) 프론트엔드만 로컬에서 개발
차트 UI를 빠르게 고치고 싶다면 대시보드만 로컬 dev 서버로 띄울 수 있습니다.
백엔드는 compose로 켜둔 상태여야 합니다 (`localhost:8000`).
```bash
cd dashboard
npm install
npm run dev        # http://localhost:5173 (핫 리로드)
```
`vite.config.ts` 의 프록시가 `/api` 요청을 `localhost:8000` 으로 전달하므로 CORS 설정이 필요 없습니다.

---

## 🔍 동작 확인 체크리스트

| 확인 대상 | 방법 | 기대 결과 |
| --- | --- | --- |
| Kafka 기동 | `docker compose ps` | kafka 가 `healthy` |
| 메시지 수집 | Kafka UI → `crypto-trades` | 메시지 개수가 증가 |
| Spark 집계 | `docker compose logs -f spark` | `[batch N] ... rows upsert 완료` |
| DB 적재 | `docker compose exec postgres psql -U crypto -c "SELECT count(*) FROM ohlc_1m;"` | 행 수 증가 |
| API | http://localhost:8000/ohlc?symbol=BTCUSDT | JSON 캔들 배열 |
| 대시보드 | http://localhost:8501 | 캔들차트 표시 |

---

## 🧠 핵심 스트리밍 개념 (이 프로젝트에서 배우는 것)

- **Producer / Consumer**: Producer(파이썬)가 Kafka에 넣고, Spark가 꺼내 쓴다. 둘은 서로를 모르고 Kafka로만 연결된다(디커플링).
- **파티션 키**: `key=symbol` 로 produce → 같은 심볼은 같은 파티션 → **순서 보장**.
- **윈도우(Window)**: 1분 텀블링 윈도우로 OHLC 집계 (`spark/jobs/ohlc_stream.py`).
- **워터마크(Watermark)**: 늦게 온 이벤트를 2분까지만 받아줌 → 무한정 상태 누적 방지.
- **체크포인트(Checkpoint)**: 마지막 offset을 저장 → 재시작해도 이어서 처리(정확성).
- **upsert**: 같은 (symbol, 분) 이 다시 계산되면 덮어쓴다 (`ON CONFLICT DO UPDATE`).

---

## 📁 디렉터리 구조

```
crypto-realtime-dashboard/
├── docker-compose.yml       # 0️⃣ 전체 오케스트레이션 (여기부터 보기)
├── .env.example             # 설정 값 예시 (.env로 복사)
├── producer/                # 1️⃣ 거래소 WS → Kafka
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── spark/                   # 2️⃣ Kafka → 집계 → Postgres
│   ├── jobs/ohlc_stream.py
│   └── Dockerfile
├── db/
│   └── init.sql             # 3️⃣ 테이블 스키마 (최초 기동 시 자동 실행)
├── backend/                 # 4️⃣ Postgres → REST API
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
├── dashboard/               # 5️⃣ API → 실시간 차트 (React + Vite + lightweight-charts)
│   ├── src/
│   │   ├── main.tsx         #   진입점 (여기부터 실행)
│   │   ├── App.tsx          #   폴링 + 상태 + 레이아웃
│   │   ├── api.ts           #   백엔드 호출 + 타입
│   │   └── components/      #   CandleChart, SymbolSelector
│   ├── nginx.conf           #   정적 서빙 + /api 프록시 (CORS 회피)
│   ├── package.json
│   └── Dockerfile           #   멀티스테이지: node 빌드 → nginx 서빙
└── README.md
```

---

## 🛠️ 트러블슈팅

- **Spark가 Kafka에 못 붙어요**: 컨테이너 내부에서는 `kafka:9092`(INTERNAL), 호스트에서는 `localhost:19092`(EXTERNAL)를 써야 합니다. 버전은 Spark 3.5 ↔ `spark-sql-kafka-0-10_2.12:3.5.1` 로 맞춰져 있습니다.
- **대시보드에 "데이터 없음"**: Producer→Kafka→Spark→DB 까지 최소 1~2분 걸립니다. `docker compose logs -f spark` 로 upsert 로그를 먼저 확인하세요.
- **스키마를 바꿨는데 반영이 안 돼요**: `init.sql` 은 DB **최초 생성 시 1회만** 실행됩니다. `docker compose down -v` 로 볼륨을 지운 뒤 다시 올리세요.
- **거래소 연결 오류**: Binance 시장 데이터 스트림은 무료/무인증이지만 단일 연결이 24시간 후 끊깁니다. 연결이 끊겨도 Producer가 자동 재연결합니다. 일부 지역에서 `stream.binance.com` 접속이 막히면 `producer/main.py` 의 `BINANCE_WS_BASE` 를 `wss://data-stream.binance.vision/stream` (기본값) 그대로 두거나 대체 엔드포인트로 바꿔보세요.

---

## 🧭 다음 단계 (후속 아이디어)

- 거래량 급증 탐지 → `price_alert` 테이블 채우기 (스키마는 이미 준비됨).
- 이동평균/볼린저 밴드 등 추가 지표.
- 여러 거래소 동시 수집 → 거래소 간 가격차(김프) 모니터링.
- Grafana로 대시보드 교체, Cassandra로 저장소 교체 실습.
