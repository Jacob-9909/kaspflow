"""
Signal Worker: Kafka(news-raw) -> PostgreSQL(news_raw, news_signal)
============================================================
[이 파일이 하는 일]
  1. news-raw 토픽을 구독한다 (consumer group = signal-worker, 오프셋 수동 커밋).
  2. 메시지 1건마다: 원문 저장 -> 종목 매칭 -> (Laya 분류) -> 신호 upsert.
  3. DB 기록이 끝난 '뒤에' 오프셋을 커밋한다. 재시작으로 같은 메시지가 다시 와도 upsert 라서 결과가 같다.

[두 가지 모드]
  LAYA_ENABLED=false (기본, M2)  저장 전용. 모델·torch 없이 news_raw 만 채운다. 메모리 ~300MB.
  LAYA_ENABLED=true  (M3)        위 + 종목별 Laya 분류 -> news_signal. 모델 1개 상주.
  M2 로 쌓아 둔 원문은 모드를 켠 뒤  `python main.py --backfill`  로 한 번 분류한다.

[데이터 흐름에서의 위치]
  Kafka(news-raw)  ->  [이 Worker]  ->  Postgres(news_raw / news_signal)  ->  backend
                           ^^^^^^^^^ 여기

[읽는 순서 추천]
  main() -> run_consumer() -> process_message() -> (matching.match_symbols, classifier, store)

설계: docs/news-signal-design.md 4.2절
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from typing import Any

from classifier import Classifier, build_text
from matching import SymbolBook, load_book, match_symbols
from store import PgStore, dsn_from_env, parse_message

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
NEWS_TOPIC = os.getenv("NEWS_TOPIC", "news-raw")
GROUP_ID = os.getenv("SIGNAL_GROUP_ID", "signal-worker")
LAYA_ENABLED = os.getenv("LAYA_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
MAX_BACKOFF = 60
STATS_EVERY_SECONDS = 300


# ------------------------------------------------------------
# 메시지 1건 처리 (Kafka 와 무관한 로직 — 테스트 대상)
# ------------------------------------------------------------
def classify_article(
    article_id: str, title: str, body: str | None, collected_at: Any,
    store: PgStore, book: SymbolBook, classifier: Classifier,
) -> list[str]:
    """이미 저장된 원문 1건을 분류해 news_signal 에 upsert 한다. 저장된 심볼 목록을 돌려준다."""
    text = build_text(title, body)
    symbols = match_symbols(f"{title}\n{body or ''}", book)
    results = []
    if symbols:
        # 종목마다 relevance/sentiment/event_type 를 한 번의 호출로 묻는다
        for sym in symbols:
            results.append((sym, classifier.classify_coin(text, book.name_of(sym))))
    else:
        # 매칭이 없으면 시장 전체 기사인지 한 번 묻고, 맞을 때만 MARKET 으로 저장한다
        market = classifier.classify_market(text, book.market_name)
        if market is not None:
            results.append((book.market_symbol, market))
    if results:
        store.upsert_signals(article_id, collected_at, classifier.model_version, results)
    return [sym for sym, _ in results]


def process_message(
    raw: bytes | str, store: PgStore, book: SymbolBook, classifier: Classifier | None,
) -> str:
    """news-raw 메시지 1건을 처리하고 결과 상태를 돌려준다.

    상태: 'stored'(새 원문, 저장 전용) | 'duplicate'(이미 처리됨) | 'classified' | 'unmatched'(신호 없음)
    형식이 틀린 메시지는 ValueError (호출자가 건너뛰고 커밋한다).
    원문은 분류보다 '먼저' 커밋한다: 분류가 실패해도 원문은 남고 --backfill 로 다시 분류할 수 있다.
    """
    msg = parse_message(raw)
    inserted, collected_at = store.insert_raw(msg)
    store.commit()

    if classifier is None:
        return "stored" if inserted else "duplicate"
    if not inserted:
        if store.has_signal(msg["article_id"], classifier.model_version):
            return "duplicate"
        # 신호 없는 중복: 종목 매칭이 있으면 지난 분류가 실패한 것이니 다시 분류한다.
        # 매칭이 없으면 '시장 전체 아님'으로 이미 버려진 기사일 가능성이 커서 다시 묻지 않는다.
        # collector 가 재시작마다 48시간치를 다시 보내 이런 기사가 Laya 를 수백 번 다시 타던 낭비 (2026-10-21).
        # ponytail: 시장 전체 질문 도중 실패한 기사는 여기서 다시 안 묻는다 -> --backfill 로 채운다.
        if not match_symbols(f"{msg['title']}\n{msg['body'] or ''}", book):
            return "duplicate"

    symbols = classify_article(msg["article_id"], msg["title"], msg["body"], collected_at, store, book, classifier)
    store.commit()
    return "classified" if symbols else "unmatched"


# ------------------------------------------------------------
# Kafka consumer 루프
# ------------------------------------------------------------
def build_consumer() -> Any:
    from confluent_kafka import Consumer  # 단위 테스트가 C 라이브러리 없이 돌게 지연 import

    return Consumer({
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "group.id": GROUP_ID,
        "auto.offset.reset": "earliest",  # 처음 붙으면 쌓여 있던 것부터
        "enable.auto.commit": False,      # DB 기록 후에만 수동 커밋
        "client.id": "signal-worker",
    })


class Stopper:
    """SIGINT/SIGTERM 로 종료를 알리는 플래그 (대기 중에도 즉시 깨어난다)."""

    def __init__(self) -> None:
        self._ev = threading.Event()

    def set(self, *_: Any) -> None:
        self._ev.set()

    def is_set(self) -> bool:
        return self._ev.is_set()

    def wait(self, seconds: float) -> None:
        self._ev.wait(seconds)


def connect_store(stop: Stopper) -> PgStore | None:
    """DB 에 붙을 때까지 지수 백오프로 재시도한다. 종료 요청이 오면 None."""
    backoff = 1
    while not stop.is_set():
        try:
            return PgStore(dsn_from_env())
        except Exception as e:
            print(f"[DB 연결 실패] {e!r} -> {backoff}초 후 재시도")
            stop.wait(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)
    return None


def run_consumer(book: SymbolBook, classifier: Classifier | None, stop: Stopper) -> None:
    from confluent_kafka import TopicPartition

    consumer = build_consumer()
    consumer.subscribe([NEWS_TOPIC])
    store = connect_store(stop)
    stats = {"stored": 0, "duplicate": 0, "classified": 0, "unmatched": 0, "bad_message": 0, "retry": 0}
    last_report = time.time()
    backoff = 1
    mode = f"classify({classifier.model_version})" if classifier else "store-only"
    print(f"[시작] topic={NEWS_TOPIC} group={GROUP_ID} mode={mode}")

    try:
        while not stop.is_set() and store is not None:
            msg = consumer.poll(1.0)
            if msg is None:
                pass
            elif msg.error():
                print(f"[consumer 오류] {msg.error()}")
            else:
                try:
                    status = process_message(msg.value(), store, book, classifier)
                    stats[status] += 1
                    backoff = 1
                except ValueError as e:  # 형식이 틀린 메시지: 재시도해도 소용없으니 건너뛴다
                    print(f"[잘못된 메시지] offset={msg.offset()}: {e}")
                    stats["bad_message"] += 1
                except Exception as e:  # DB 끊김 등: 롤백하고 재연결한 뒤 같은 메시지를 다시 처리한다
                    stats["retry"] += 1
                    print(f"[처리 실패] offset={msg.offset()}: {e!r} -> {backoff}초 후 재시도")
                    store.rollback()
                    store.close()
                    stop.wait(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)
                    store = connect_store(stop)
                    consumer.seek(TopicPartition(msg.topic(), msg.partition(), msg.offset()))
                    continue
                consumer.commit(message=msg, asynchronous=False)  # DB 기록 뒤에 커밋
            if time.time() - last_report >= STATS_EVERY_SECONDS:
                print(f"[통계] {stats}")
                last_report = time.time()
    finally:
        print(f"[종료] {stats}")
        consumer.close()
        if store is not None:
            store.close()


# ------------------------------------------------------------
# 백필: 신호가 없는 원문을 현재 모델로 분류
# ------------------------------------------------------------
def run_backfill(store: PgStore, book: SymbolBook, classifier: Classifier, batch: int = 100) -> dict[str, int]:
    done = {"classified": 0, "unmatched": 0, "failed": 0}
    after = ""
    while True:
        rows = store.unclassified(classifier.model_version, batch, after)
        if not rows:
            break
        for r in rows:
            after = r["article_id"]
            try:
                syms = classify_article(r["article_id"], r["title"], r["body"], r["collected_at"], store, book, classifier)
                store.commit()
                done["classified" if syms else "unmatched"] += 1
            except Exception as e:
                store.rollback()
                done["failed"] += 1
                print(f"[백필 실패] {r['article_id']}: {e!r}")
        print(f"[백필] {done}")
    return done


def make_classifier() -> Classifier | None:
    if not LAYA_ENABLED:
        return None
    from classifier import LayaClassifier

    return LayaClassifier()


def main() -> None:
    ap = argparse.ArgumentParser(description="news-raw -> news_raw / news_signal")
    ap.add_argument("--backfill", action="store_true",
                    help="신호가 없는 원문을 현재 모델로 분류하고 끝낸다 (LAYA_ENABLED=true 필요)")
    args = ap.parse_args()

    book = load_book()
    classifier = make_classifier()
    if args.backfill:
        if classifier is None:
            sys.exit("--backfill 은 LAYA_ENABLED=true 가 필요합니다")
        print(f"[백필 시작] model_version={classifier.model_version}")
        store = PgStore(dsn_from_env())
        try:
            run_backfill(store, book, classifier)
        finally:
            store.close()
        return

    stop = Stopper()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop.set)
    run_consumer(book, classifier, stop)


if __name__ == "__main__":
    main()
