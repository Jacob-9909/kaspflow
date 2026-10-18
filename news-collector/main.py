"""
News Collector: 코인 매체 공식 RSS -> Kafka (news-raw)
============================================================
[이 파일이 하는 일]
  1. 설정된 RSS 피드를 소스별로 5분(NEWS_POLL_SECONDS)마다 폴링한다. (조건부 GET: ETag/Last-Modified)
  2. 기사를 표준 메시지(JSON)로 정규화한다. 시각은 전부 UTC, URL 은 추적 파라미터를 걷어 SHA-256 해시.
  3. Kafka 의 `news-raw` 토픽으로 produce 한다. (파티션 키 = source)
  4. 소스별로 독립된 루프 + 지수 백오프라서 한 소스가 죽어도 다른 소스는 계속 돈다.

[상태]
  영속 상태가 없다. 프로세스 안의 seen 집합으로 '같은 기사를 5분마다 또 보내는 낭비'만 줄인다.
  재시작하면 한 번 더 나갈 수 있고, 최종 중복 판정은 consumer(signal-worker 의 news_raw PK)가 한다.

[데이터 흐름에서의 위치]
  RSS  ->  [이 Collector]  ->  Kafka(news-raw)  ->  signal-worker  ->  Postgres(news_raw, news_signal)
              ^^^^^^^^^ 여기
  구조는 producer/main.py 를 따른다. (재연결 백오프, 시그널 핸들러, flush 종료)

[읽는 순서 추천]
  main() -> poll_feed_forever() -> poll_once() -> entry_to_message() / normalize_url()

설계: docs/news-signal-design.md 4.1절
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import os
import re
import signal
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx

# ------------------------------------------------------------
# 설정: 환경 변수에서 읽되, 없으면 기본값 사용
# ------------------------------------------------------------
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
NEWS_TOPIC = os.getenv("NEWS_TOPIC", "news-raw")
NEWS_POLL_SECONDS = int(os.getenv("NEWS_POLL_SECONDS", "300"))
# 토픽을 우리가 만들 때의 보존 기간. worker 배포가 늦어져도 이 기간만큼은 Kafka 가 버퍼가 된다.
NEWS_RETENTION_DAYS = int(os.getenv("NEWS_RETENTION_DAYS", "14"))
NEWS_PARTITIONS = int(os.getenv("NEWS_PARTITIONS", "3"))
SEEN_MAX = int(os.getenv("NEWS_SEEN_MAX", "20000"))

# 실패가 이어져도 이 값(초) 이상으로는 안 기다린다. (producer 는 30초지만 폴링 서비스라 더 길게)
MAX_BACKOFF = 300
HTTP_TIMEOUT = 20.0
BODY_MAX_CHARS = 2000
# 발행 후 이 시간(시)이 지난 항목은 보내지 않는다. 피드에 섞인 몇 달 전 글(예: Decrypt 팟캐스트)이
# 최근 뉴스 목록·라벨 표본을 차지하는 것을 막는다. 전략의 max_age_min(6h)보다 넉넉하게 둔다. 0 = 끔
NEWS_MAX_AGE_HOURS = float(os.getenv("NEWS_MAX_AGE_HOURS", "48"))
USER_AGENT = os.getenv("NEWS_USER_AGENT", "kaspflow-news-collector/1.0 (+https://github.com/Jacob-9909/kaspflow)")
# 소스마다 한꺼번에 요청하지 않도록 첫 폴링을 이 간격(초)씩 어긋나게 한다.
START_STAGGER_SECONDS = 2.0


@dataclass(frozen=True)
class Feed:
    source: str  # 메시지의 source 이자 파티션 키. 같은 사이트의 여러 피드는 같은 이름을 쓴다.
    url: str
    lang: str = "en"


# 2026-10-05 동작 확인된 공식 RSS (docs/news-signal-design.md 4.1절).
# Blockworks(2026-01 이후 새 기사 없음), CryptoSlate(연속 요청 제한)는 제외.
DEFAULT_FEEDS: list[Feed] = [
    Feed("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml"),
    Feed("cointelegraph", "https://cointelegraph.com/rss"),
    Feed("cointelegraph", "https://cointelegraph.com/rss/tag/bitcoin"),
    Feed("cointelegraph", "https://cointelegraph.com/rss/tag/ethereum"),
    Feed("cointelegraph", "https://cointelegraph.com/rss/tag/altcoins"),
    Feed("theblock", "https://www.theblock.co/rss.xml"),
    Feed("decrypt", "https://decrypt.co/feed"),
    Feed("bitcoinmagazine", "https://bitcoinmagazine.com/feed"),
]


def parse_feeds(spec: str | None) -> list[Feed]:
    """NEWS_FEEDS 문자열 -> Feed 리스트. 비어 있으면 기본 목록.

    형식: "source|url[|lang]" 항목을 ';' 또는 줄바꿈으로 구분.
      예) coindesk|https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml;decrypt|https://decrypt.co/feed|en
    """
    if not spec or not spec.strip():
        return list(DEFAULT_FEEDS)
    feeds: list[Feed] = []
    for item in re.split(r"[;\n]", spec):
        item = item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split("|")]
        if len(parts) not in (2, 3) or not parts[0] or not parts[1].startswith(("http://", "https://")):
            raise ValueError(f"NEWS_FEEDS 항목 형식 오류 (source|url[|lang]): {item!r}")
        feeds.append(Feed(parts[0].lower(), parts[1], parts[2] if len(parts) == 3 else "en"))
    if not feeds:
        raise ValueError("NEWS_FEEDS 에 유효한 항목이 없습니다")
    return feeds


# ------------------------------------------------------------
# 정규화 (순수 함수 — 네트워크/Kafka 없이 테스트 가능)
# ------------------------------------------------------------
# 같은 기사를 다른 글자로 만드는 추적용 쿼리 파라미터
_TRACKING_KEYS = {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "ref_src", "ref_url", "cmpid"}


def normalize_url(url: str) -> str:
    """중복 판정용 URL 정규화.

    - 스킴은 https 로 통일, 호스트는 소문자 + 'www.' 제거, 기본 포트 제거
    - #fragment 와 추적 파라미터(utm_*, fbclid 등) 제거, 남은 쿼리는 키 순으로 정렬
    - 경로 끝의 '/' 제거
    """
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_KEYS
    )
    path = parts.path.rstrip("/")
    return urlunsplit(("https", host, path, urlencode(query), ""))


def article_id(url: str) -> str:
    """정규화한 URL 의 SHA-256(hex). signal-worker 의 news_raw PK 이자 중복 판정 키."""
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


_SCRIPT_STYLE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


def strip_html(text: str | None) -> str:
    """HTML 태그/엔티티를 제거하고 공백을 정리한다."""
    if not text:
        return ""
    text = _SCRIPT_STYLE.sub(" ", text)
    text = _TAGS.sub(" ", text)
    return _SPACES.sub(" ", html.unescape(text)).strip()


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _published_at(entry: Any, collected_at: datetime) -> datetime | None:
    """발행 시각(UTC). feedparser 가 timezone 을 반영해 UTC struct_time 으로 준다. 없으면 None.

    수집 시각보다 미래로 찍힌 값(피드 시계 오류)은 수집 시각으로 당겨 둔다.
    그대로 두면 '오래된 기사 제외' 필터를 아무 때나 통과해 버린다.
    """
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if not st:
        return None
    try:
        published = datetime(*st[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return min(published, collected_at)


def entry_to_message(
    feed: Feed, entry: Any, collected_at: datetime, max_age_hours: float | None = None
) -> dict[str, Any] | None:
    """feedparser 항목 1건 -> news-raw 메시지. 링크·제목이 없거나 발행 후 max_age_hours 가 지났으면 None.

    발행 시각이 없는 항목은 오래됐는지 알 수 없어 보낸다 (전략은 이런 기사를 쓰지 않는다).
    """
    url = (entry.get("link") or "").strip()
    title = strip_html(entry.get("title"))
    if not url or not title:
        return None

    raw_body = entry.get("summary")
    if not raw_body and entry.get("content"):
        raw_body = entry["content"][0].get("value")
    body = strip_html(raw_body)[:BODY_MAX_CHARS]

    published = _published_at(entry, collected_at)
    if max_age_hours is None:
        max_age_hours = NEWS_MAX_AGE_HOURS
    if published and max_age_hours > 0 and collected_at - published > timedelta(hours=max_age_hours):
        return None
    return {
        "article_id": article_id(url),
        "source": feed.source,
        "url": url,
        "title": title,
        "body": body,
        "lang": feed.lang,
        "published_at": _iso_z(published) if published else None,
        "collected_at": _iso_z(collected_at),
    }


class SeenSet:
    """최근 본 article_id 를 최대 maxsize 개까지 기억한다 (가장 오래된 것부터 버림)."""

    def __init__(self, maxsize: int) -> None:
        self._max = max(1, maxsize)
        self._items: OrderedDict[str, None] = OrderedDict()

    def add(self, key: str) -> bool:
        """새로 넣었으면 True, 이미 있었으면 False."""
        if key in self._items:
            self._items.move_to_end(key)
            return False
        self._items[key] = None
        if len(self._items) > self._max:
            self._items.popitem(last=False)
        return True

    def discard(self, key: str) -> None:
        self._items.pop(key, None)

    def __len__(self) -> int:
        return len(self._items)


# ------------------------------------------------------------
# 폴링
# ------------------------------------------------------------
async def poll_once(
    feed: Feed,
    client: httpx.AsyncClient,
    cache: dict[str, dict[str, str]],
    seen: SeenSet,
    producer: Any,
    topic: str = NEWS_TOPIC,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    """피드 1개를 한 번 폴링해 새 기사를 produce 한다. produce 한 건수를 돌려준다.

    cache[feed.url] 에 ETag/Last-Modified 를 저장해 다음 요청에 조건부로 보낸다(304 면 아무것도 안 함).
    네트워크/HTTP/파싱 오류는 예외로 올려서 호출자(poll_feed_forever)가 백오프하게 한다.
    """
    headers: dict[str, str] = {}
    state = cache.get(feed.url, {})
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]

    resp = await client.get(feed.url, headers=headers)
    collected_at = now()  # 응답을 받은 시각 = 시스템이 이 기사들을 알게 된 시각
    if resp.status_code == 304:
        return 0
    resp.raise_for_status()

    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"피드 파싱 실패: {parsed.get('bozo_exception')!r}")

    new_state = {}
    if resp.headers.get("etag"):
        new_state["etag"] = resp.headers["etag"]
    if resp.headers.get("last-modified"):
        new_state["last_modified"] = resp.headers["last-modified"]
    cache[feed.url] = new_state

    produced = 0
    for entry in parsed.entries:
        msg = entry_to_message(feed, entry, collected_at)
        if msg is None or not seen.add(msg["article_id"]):
            continue
        if _produce(producer, topic, msg, seen):
            produced += 1
    producer.poll(0)
    return produced


def _produce(producer: Any, topic: str, msg: dict[str, Any], seen: SeenSet) -> bool:
    """메시지 1건을 produce 한다. 실패하면 seen 에서 빼서 다음 폴링 때 다시 시도하게 한다."""
    aid = msg["article_id"]

    def on_delivery(err: Any, _msg: Any) -> None:
        if err is not None:
            print(f"[produce 실패] {msg['source']} {msg['url']}: {err}")
            seen.discard(aid)

    try:
        producer.produce(
            topic=topic,
            key=msg["source"],
            value=json.dumps(msg, ensure_ascii=False),
            callback=on_delivery,
        )
    except BufferError:  # 로컬 큐가 가득 참
        seen.discard(aid)
        producer.poll(1)
        return False
    return True


def next_backoff(backoff: int) -> int:
    """연속 실패 시 대기 시간(초)을 2배로, 상한 MAX_BACKOFF."""
    return min(backoff * 2, MAX_BACKOFF)


async def _sleep_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    """seconds 만큼 쉬되, stop_event 가 설정되면 즉시 깨어난다."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def poll_feed_forever(
    feed: Feed,
    client: httpx.AsyncClient,
    seen: SeenSet,
    producer: Any,
    stop_event: asyncio.Event,
    interval: float,
    start_delay: float = 0.0,
) -> None:
    """피드 1개를 계속 폴링하는 루프. 실패하면 지수 백오프, 성공하면 interval 로 복귀한다."""
    cache: dict[str, dict[str, str]] = {}
    backoff = 1
    await _sleep_or_stop(stop_event, start_delay)
    while not stop_event.is_set():
        try:
            n = await poll_once(feed, client, cache, seen, producer)
            print(f"[poll] {feed.source} {feed.url} new={n} seen={len(seen)}")
            backoff = 1
            wait = interval
        except Exception as e:  # 네트워크/HTTP/파싱 오류 전부: 이 소스만 백오프하고 계속
            print(f"[poll 실패] {feed.source} {feed.url}: {e!r} -> {backoff}초 후 재시도")
            wait = backoff
            backoff = next_backoff(backoff)
        await _sleep_or_stop(stop_event, wait)


# ------------------------------------------------------------
# Kafka
# ------------------------------------------------------------
def build_producer() -> Any:
    """confluent-kafka Producer. (producer/main.py 와 같은 안정성 옵션)

    confluent_kafka 는 여기서 import 한다: 정규화/폴링 로직의 단위 테스트가 C 라이브러리 없이 돌게 하려는 것.
    """
    from confluent_kafka import Producer

    return Producer({
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "acks": "all",
        "enable.idempotence": True,
        "linger.ms": 50,
        "compression.type": "lz4",
        "client.id": "news-collector",
    })


def ensure_topic() -> None:
    """news-raw 가 없으면 보존 기간을 지정해 만든다. 이미 있으면 건드리지 않는다.

    실패해도 치명적이지 않다(Kafka 기본 auto-create 로 첫 produce 때 만들어진다, 보존은 브로커 기본값).
    """
    from confluent_kafka.admin import AdminClient, NewTopic

    admin = AdminClient({"bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS})
    topic = NewTopic(
        NEWS_TOPIC,
        num_partitions=NEWS_PARTITIONS,
        replication_factor=1,  # 단일 브로커
        config={"retention.ms": str(int(timedelta(days=NEWS_RETENTION_DAYS).total_seconds() * 1000))},
    )
    for name, future in admin.create_topics([topic]).items():
        try:
            future.result(timeout=30)
            print(f"[토픽 생성] {name} (partitions={NEWS_PARTITIONS}, retention={NEWS_RETENTION_DAYS}d)")
        except Exception as e:
            if "TOPIC_ALREADY_EXISTS" in str(e):
                print(f"[토픽 확인] {name} 이미 있음")
            else:
                print(f"[토픽 생성 실패] {name}: {e!r} (auto-create 에 맡기고 계속)")


async def run(producer: Any, feeds: list[Feed], stop_event: asyncio.Event) -> None:
    seen = SeenSet(SEEN_MAX)
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.5"},
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
    ) as client:
        print(f"[수집 시작] feeds={len(feeds)} interval={NEWS_POLL_SECONDS}s -> topic={NEWS_TOPIC}")
        await asyncio.gather(*(
            poll_feed_forever(f, client, seen, producer, stop_event, NEWS_POLL_SECONDS, i * START_STAGGER_SECONDS)
            for i, f in enumerate(feeds)
        ))


def main() -> None:
    feeds = parse_feeds(os.getenv("NEWS_FEEDS"))
    try:
        ensure_topic()
    except Exception as e:  # AdminClient 자체가 안 만들어지는 등
        print(f"[토픽 보장 건너뜀] {e!r}")
    producer = build_producer()
    stop_event = asyncio.Event()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # Ctrl+C / docker stop(SIGTERM) 시 깔끔하게 종료
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        loop.run_until_complete(run(producer, feeds, stop_event))
    finally:
        print("[종료] 남은 메시지 flush 중...")
        producer.flush(10)


if __name__ == "__main__":
    main()
