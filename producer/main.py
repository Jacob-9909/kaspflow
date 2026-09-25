"""
Producer: 거래소(Binance) WebSocket 체결 스트림 -> Kafka
============================================================
[이 파일이 하는 일]
  1. Binance WebSocket에 연결해 지정한 심볼들의 "체결(trade)" 이벤트를 구독한다.
  2. 받은 이벤트를 우리 표준 포맷(JSON)으로 변환한다.
  3. Kafka의 `crypto-trades` 토픽으로 produce 한다.
  4. 연결이 끊기면 자동으로 재연결한다 (지수 백오프).

[왜 Binance인가]
  글로벌 현물 거래량이 가장 크고, 시장 데이터 WebSocket이 무료/무인증이다.
  체결 데이터 "수신"에는 사실상 제한이 없다(초당 제한은 내가 보내는 요청 기준).
  단, 단일 연결은 24시간 후 서버가 끊으므로 아래 재연결 로직이 이를 처리한다.

[데이터 흐름에서의 위치]
  거래소 WS  ->  [이 Producer]  ->  Kafka  ->  Spark  ->  Postgres  ->  API  ->  대시보드
                     ^^^^^^^^^ 여기

[읽는 순서 추천]
  main() -> run_forever() -> stream_once() -> handle_message()
"""
import asyncio
import json
import os
import signal
from datetime import datetime, timezone

import websockets
from confluent_kafka import Producer

# ------------------------------------------------------------
# 설정: 환경 변수에서 읽되, 없으면 기본값 사용
# (docker-compose.yml 의 environment 로 주입된다)
# ------------------------------------------------------------
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "crypto-trades")
# "BTCUSDT,ETHUSDT" 형태의 문자열을 리스트로 변환.
# Binance 현물 심볼은 USDT 마켓 기준 (예: BTCUSDT = 비트코인/테더).
SYMBOLS = [s.strip().upper() for s in os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT,XRPUSDT").split(",") if s.strip()]

# Binance 현물 WebSocket (combined stream 방식).
#   - 시장 데이터 전용 엔드포인트(data-stream)를 쓴다. 인증 불필요, 무료.
#   - combined stream: /stream?streams=btcusdt@trade/ethusdt@trade/...
#     (스트림 이름은 반드시 소문자. 응답은 {"stream":..., "data":...} 로 감싸짐)
BINANCE_WS_BASE = "wss://data-stream.binance.vision/stream"


def build_stream_url(symbols: list[str]) -> str:
    """심볼 리스트 -> Binance combined stream URL 생성.

    예) ["BTCUSDT","ETHUSDT"] -> ".../stream?streams=btcusdt@trade/ethusdt@trade"
    """
    streams = "/".join(f"{s.lower()}@trade" for s in symbols)
    return f"{BINANCE_WS_BASE}?streams={streams}"


# 재연결 백오프 상한(초). 실패가 이어져도 이 값 이상으로는 안 기다린다.
MAX_BACKOFF = 30


def build_producer() -> Producer:
    """confluent-kafka Producer 인스턴스를 만든다.

    주요 옵션 설명:
      - bootstrap.servers : 붙을 Kafka 주소
      - acks=all          : 리더+팔로워가 다 받았다고 확인해야 성공 처리 (안정성)
      - enable.idempotence: 재전송 시 메시지 중복 방지 (정확히 한 번 produce)
      - linger.ms=50      : 50ms 동안 메시지를 모아서 배치로 전송 (처리량↑)
    """
    return Producer({
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "acks": "all",
        "enable.idempotence": True,
        "linger.ms": 50,
        "compression.type": "lz4",
        "client.id": "crypto-producer",
    })


def delivery_report(err, msg):
    """produce 결과 콜백. 실패한 메시지만 로그로 남긴다."""
    if err is not None:
        print(f"[produce 실패] {err}")


def handle_message(raw: bytes, producer: Producer) -> None:
    """Binance에서 받은 원본 메시지 1건을 표준 포맷으로 변환해 Kafka로 보낸다.

    combined stream 응답은 다음처럼 감싸져 온다:
      {"stream": "btcusdt@trade", "data": { ...실제 trade 이벤트... }}

    Binance trade 이벤트(data) 필드:
      s = Symbol("BTCUSDT"), p = price(문자열), q = quantity(문자열),
      T = trade time(ms), m = 매수자가 maker인지 여부
        - m=true  : 매수자가 maker -> 시장가 매도가 체결을 일으킴 -> side "ASK"
        - m=false : 매도자가 maker -> 시장가 매수가 체결을 일으킴 -> side "BID"
    """
    msg = json.loads(raw)

    # combined stream은 data 안에 실제 이벤트가 있다.
    # (혹시 raw 스트림이면 data 키가 없으므로 msg 자체를 쓴다)
    data = msg.get("data", msg)

    # 구독 응답/에러 등 trade 이벤트가 아닌 메시지는 건너뛴다.
    if data.get("e") != "trade":
        return

    # 우리 파이프라인 표준 스키마 (설계서 3.1 절과 동일)
    event = {
        "symbol": data["s"],
        "price": float(data["p"]),
        "volume": float(data["q"]),
        "side": "ASK" if data.get("m") else "BID",
        # Binance는 ms 단위 epoch(T). ISO8601 UTC 문자열로 변환해 둔다.
        "trade_ts": datetime.fromtimestamp(data["T"] / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "source": "binance",
    }

    # key=symbol 로 지정하면 같은 심볼은 항상 같은 파티션에 들어가 순서가 보장된다.
    producer.produce(
        topic=KAFKA_TOPIC,
        key=event["symbol"],
        value=json.dumps(event),
        callback=delivery_report,
    )
    # produce는 비동기 큐에 넣기만 함. poll()이 실제 전송/콜백 처리를 진행시킨다.
    producer.poll(0)


async def stream_once(producer: Producer) -> None:
    """WebSocket에 한 번 연결해서, 끊길 때까지 메시지를 수신/전달한다.

    Binance는 combined stream URL에 구독할 스트림을 이미 담아 접속하므로
    Upbit처럼 별도의 구독 메시지를 보낼 필요가 없다 (URL이 곧 구독).

    ping/pong: Binance 서버는 20초마다 ping을 보내고, 1분 내 pong이 없으면
    연결을 끊는다. websockets 라이브러리가 서버 ping에 자동으로 pong을 응답한다.
    """
    url = build_stream_url(SYMBOLS)
    async with websockets.connect(url, ping_interval=None) as ws:
        # ping_interval=None: 클라이언트가 먼저 ping을 보내지 않고,
        # 서버가 보내는 ping에 대한 자동 pong 응답만 맡긴다 (Binance 권장 방향).
        print(f"[구독 시작] symbols={SYMBOLS} -> topic={KAFKA_TOPIC}")

        # 메시지를 계속 수신 (연결이 살아있는 동안 무한 루프)
        async for raw in ws:
            handle_message(raw, producer)


async def run_forever(producer: Producer, stop_event: asyncio.Event) -> None:
    """연결이 끊겨도 계속 재연결하는 상위 루프 (지수 백오프).

    성공적으로 연결되면 백오프를 초기화하고, 실패하면 대기 시간을 2배씩 늘린다.
    """
    backoff = 1
    while not stop_event.is_set():
        try:
            await stream_once(producer)
        except Exception as e:  # 네트워크 오류/거래소 점검 등 모든 예외를 잡아 재연결
            print(f"[연결 끊김] {e!r} -> {backoff}초 후 재연결")
            # stop_event가 설정되면 대기 도중에도 즉시 빠져나온다
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, MAX_BACKOFF)
        else:
            backoff = 1  # 정상 종료(드묾)면 백오프 리셋


def main() -> None:
    producer = build_producer()
    stop_event = asyncio.Event()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Ctrl+C / docker stop(SIGTERM) 시 깔끔하게 종료하기 위한 시그널 핸들러
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        loop.run_until_complete(run_forever(producer, stop_event))
    finally:
        # 큐에 남은 메시지를 최대 5초간 마저 전송하고 종료
        print("[종료] 남은 메시지 flush 중...")
        producer.flush(5)


if __name__ == "__main__":
    main()
