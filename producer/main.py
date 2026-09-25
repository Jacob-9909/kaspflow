"""
Producer: 거래소(Upbit) WebSocket 체결 스트림 -> Kafka
============================================================
[이 파일이 하는 일]
  1. Upbit WebSocket에 연결해 지정한 심볼들의 "체결(trade)" 이벤트를 구독한다.
  2. 받은 이벤트를 우리 표준 포맷(JSON)으로 변환한다.
  3. Kafka의 `crypto-trades` 토픽으로 produce 한다.
  4. 연결이 끊기면 자동으로 재연결한다 (지수 백오프).

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
# "KRW-BTC,KRW-ETH" 형태의 문자열을 리스트로 변환
SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "KRW-BTC,KRW-ETH,KRW-XRP").split(",") if s.strip()]

UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"

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
    """Upbit에서 받은 원본 메시지 1건을 표준 포맷으로 변환해 Kafka로 보낸다.

    Upbit trade 이벤트 예시 필드:
      cd = code(심볼, "KRW-BTC"), tp = trade price, tv = trade volume,
      ab = ask/bid, ttms = trade timestamp(ms)
    """
    data = json.loads(raw)

    # 우리 파이프라인 표준 스키마 (설계서 3.1 절과 동일)
    event = {
        "symbol": data["cd"],
        "price": float(data["tp"]),
        "volume": float(data["tv"]),
        "side": "BID" if data.get("ab") == "BID" else "ASK",
        # Upbit는 ms 단위 epoch. ISO8601 UTC 문자열로 변환해 둔다.
        "trade_ts": datetime.fromtimestamp(data["ttms"] / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "source": "upbit",
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
    """WebSocket에 한 번 연결해서, 끊길 때까지 메시지를 수신/전달한다."""
    async with websockets.connect(UPBIT_WS_URL, ping_interval=20, ping_timeout=20) as ws:
        # Upbit 구독 요청 포맷: [{ticket}, {type, codes}, {format}]
        subscribe_msg = [
            {"ticket": "crypto-realtime-dashboard"},
            {"type": "trade", "codes": SYMBOLS},
            {"format": "SIMPLE"},  # 짧은 필드명(cd, tp, tv...) 사용
        ]
        await ws.send(json.dumps(subscribe_msg))
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
