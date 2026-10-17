"""
news-collector 테스트 (Kafka/네트워크 불필요)
============================================================
정규화·중복 억제·조건부 GET·실패 시 재시도를 검증한다. HTTP 는 httpx.MockTransport 로 대체한다.

실행:  cd news-collector && python -m unittest discover -s tests -v
"""
import asyncio
import json
import os
import sys
import unittest
from datetime import datetime, timezone

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as nc  # noqa: E402

NOW = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
FEED = nc.Feed("coindesk", "https://example.com/rss")

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>t</title>
<item>
  <title>Bitcoin &amp; ETF inflows <b>surge</b></title>
  <link>https://www.Example.com/news/btc-etf/?utm_source=rss&amp;utm_medium=x#top</link>
  <description>&lt;p&gt;Spot ETFs saw &lt;b&gt;record&lt;/b&gt; inflows.&lt;/p&gt;</description>
  <pubDate>Fri, 16 Oct 2026 11:30:00 +0900</pubDate>
</item>
<item>
  <title>XRP ruling expected</title>
  <link>https://example.com/news/xrp</link>
  <description>Court to rule.</description>
</item>
</channel></rss>"""


class FakeProducer:
    """confluent_kafka.Producer 대역. fail=True 면 전송 결과 콜백을 실패로 부른다."""

    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def produce(self, topic, key, value, callback):
        self.sent.append({"topic": topic, "key": key, "value": json.loads(value)})
        if self.fail:
            callback("boom", None)

    def poll(self, _timeout):
        pass


class TestNormalize(unittest.TestCase):
    def test_tracking_fragment_host_and_slash_are_ignored(self):
        a = "https://www.Example.com/news/btc/?utm_source=rss&b=2&a=1#top"
        b = "http://example.com/news/btc?a=1&b=2&fbclid=zzz"
        self.assertEqual(nc.normalize_url(a), nc.normalize_url(b))
        self.assertEqual(nc.article_id(a), nc.article_id(b))

    def test_different_paths_or_queries_differ(self):
        self.assertNotEqual(nc.article_id("https://example.com/a"), nc.article_id("https://example.com/b"))
        self.assertNotEqual(nc.article_id("https://example.com/a?id=1"), nc.article_id("https://example.com/a?id=2"))

    def test_article_id_is_sha256_hex(self):
        aid = nc.article_id("https://example.com/a")
        self.assertEqual(len(aid), 64)
        int(aid, 16)

    def test_strip_html(self):
        self.assertEqual(nc.strip_html("<p>A &amp; <b>B</b></p>\n  c"), "A & B c")
        self.assertEqual(nc.strip_html("<script>x()</script>hi"), "hi")
        self.assertEqual(nc.strip_html(None), "")


class TestParseFeeds(unittest.TestCase):
    def test_default_when_empty(self):
        self.assertEqual(nc.parse_feeds(None), nc.DEFAULT_FEEDS)
        self.assertEqual(nc.parse_feeds("  "), nc.DEFAULT_FEEDS)

    def test_custom_spec(self):
        feeds = nc.parse_feeds("CoinDesk|https://a.example/rss;decrypt|https://b.example/feed|ko\n")
        self.assertEqual(feeds, [nc.Feed("coindesk", "https://a.example/rss", "en"),
                                 nc.Feed("decrypt", "https://b.example/feed", "ko")])

    def test_bad_spec_raises(self):
        for bad in ("onlyname", "x|notaurl", "|https://a.example", ";"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                nc.parse_feeds(bad)

    def test_defaults_cover_documented_sources(self):
        self.assertEqual({f.source for f in nc.DEFAULT_FEEDS},
                         {"coindesk", "cointelegraph", "theblock", "decrypt", "bitcoinmagazine"})


class TestEntryToMessage(unittest.TestCase):
    def entries(self, xml=RSS):
        return nc.feedparser.parse(xml.encode()).entries

    def test_message_fields_and_utc_conversion(self):
        msg = nc.entry_to_message(FEED, self.entries()[0], NOW)
        self.assertEqual(msg["source"], "coindesk")
        self.assertEqual(msg["title"], "Bitcoin & ETF inflows surge")
        self.assertEqual(msg["body"], "Spot ETFs saw record inflows.")
        self.assertEqual(msg["lang"], "en")
        self.assertEqual(msg["published_at"], "2026-10-16T02:30:00Z")  # 11:30 KST -> 02:30 UTC
        self.assertEqual(msg["collected_at"], "2026-10-16T12:00:00Z")
        self.assertEqual(msg["article_id"], nc.article_id(msg["url"]))
        self.assertIn("utm_source", msg["url"])  # url 은 원본 그대로, 해시만 정규화 기준

    def test_missing_pubdate_is_none(self):
        self.assertIsNone(nc.entry_to_message(FEED, self.entries()[1], NOW)["published_at"])

    def test_future_pubdate_is_clamped_to_collected_at(self):
        xml = RSS.replace("Fri, 16 Oct 2026 11:30:00 +0900", "Sat, 17 Oct 2026 11:30:00 +0000")
        msg = nc.entry_to_message(FEED, self.entries(xml)[0], NOW)
        self.assertEqual(msg["published_at"], "2026-10-16T12:00:00Z")

    def test_body_is_truncated(self):
        xml = RSS.replace("Court to rule.", "x" * 5000)
        msg = nc.entry_to_message(FEED, self.entries(xml)[1], NOW)
        self.assertEqual(len(msg["body"]), nc.BODY_MAX_CHARS)

    def test_entry_without_link_or_title_is_skipped(self):
        self.assertIsNone(nc.entry_to_message(FEED, {"title": "t"}, NOW))
        self.assertIsNone(nc.entry_to_message(FEED, {"link": "https://example.com/a", "title": "  "}, NOW))


class TestSeenSet(unittest.TestCase):
    def test_add_and_eviction(self):
        s = nc.SeenSet(2)
        self.assertTrue(s.add("a"))
        self.assertFalse(s.add("a"))
        s.add("b")
        s.add("c")  # 용량 2 초과 -> 가장 오래 안 본 a 가 밀려남
        self.assertEqual(len(s), 2)
        self.assertTrue(s.add("a"))  # 밀려났으므로 다시 '새 것'
        s.discard("c")
        self.assertTrue(s.add("c"))


class TestPoll(unittest.IsolatedAsyncioTestCase):
    def client(self, handler):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def test_produces_new_articles_keyed_by_source(self):
        async with self.client(lambda r: httpx.Response(200, content=RSS)) as c:
            prod = FakeProducer()
            n = await nc.poll_once(FEED, c, {}, nc.SeenSet(100), prod, topic="t", now=lambda: NOW)
        self.assertEqual(n, 2)
        self.assertEqual([m["key"] for m in prod.sent], ["coindesk", "coindesk"])
        self.assertEqual({m["topic"] for m in prod.sent}, {"t"})
        self.assertEqual(prod.sent[0]["value"]["collected_at"], "2026-10-16T12:00:00Z")

    async def test_second_poll_with_same_content_produces_nothing(self):
        async with self.client(lambda r: httpx.Response(200, content=RSS)) as c:
            prod, seen, cache = FakeProducer(), nc.SeenSet(100), {}
            await nc.poll_once(FEED, c, cache, seen, prod, now=lambda: NOW)
            n = await nc.poll_once(FEED, c, cache, seen, prod, now=lambda: NOW)
        self.assertEqual(n, 0)
        self.assertEqual(len(prod.sent), 2)

    async def test_conditional_get_uses_etag_and_304_skips_parsing(self):
        calls = []

        def handler(request):
            calls.append(request.headers.get("if-none-match"))
            if request.headers.get("if-none-match") == '"v1"':
                return httpx.Response(304)
            return httpx.Response(200, content=RSS, headers={"etag": '"v1"'})

        async with self.client(handler) as c:
            prod, seen, cache = FakeProducer(), nc.SeenSet(100), {}
            first = await nc.poll_once(FEED, c, cache, seen, prod, now=lambda: NOW)
            second = await nc.poll_once(FEED, c, cache, seen, prod, now=lambda: NOW)
        self.assertEqual((first, second), (2, 0))
        self.assertEqual(calls, [None, '"v1"'])

    async def test_failed_delivery_is_retried_on_next_poll(self):
        async with self.client(lambda r: httpx.Response(200, content=RSS)) as c:
            seen, cache = nc.SeenSet(100), {}
            await nc.poll_once(FEED, c, cache, seen, FakeProducer(fail=True), now=lambda: NOW)
            ok = FakeProducer()
            n = await nc.poll_once(FEED, c, cache, seen, ok, now=lambda: NOW)
        self.assertEqual(n, 2)  # 전송 실패한 기사는 seen 에서 빠져 다시 나간다

    async def test_http_error_and_garbage_raise(self):
        async with self.client(lambda r: httpx.Response(503)) as c:
            with self.assertRaises(httpx.HTTPStatusError):
                await nc.poll_once(FEED, c, {}, nc.SeenSet(10), FakeProducer(), now=lambda: NOW)
        async with self.client(lambda r: httpx.Response(200, content=b"<html>not a feed")) as c:
            with self.assertRaises(ValueError):
                await nc.poll_once(FEED, c, {}, nc.SeenSet(10), FakeProducer(), now=lambda: NOW)

    async def test_loop_survives_failure_and_stops_on_event(self):
        attempts = {"n": 0}

        def handler(request):
            attempts["n"] += 1
            return httpx.Response(503) if attempts["n"] == 1 else httpx.Response(200, content=RSS)

        stop = asyncio.Event()
        prod = FakeProducer()
        async with self.client(handler) as c:
            task = asyncio.create_task(nc.poll_feed_forever(FEED, c, nc.SeenSet(100), prod, stop, interval=60))
            for _ in range(300):  # 첫 시도 실패 -> 1초 백오프 -> 두 번째 성공
                await asyncio.sleep(0.02)
                if prod.sent:
                    break
            stop.set()
            await asyncio.wait_for(task, timeout=3)
        self.assertEqual(attempts["n"], 2)
        self.assertEqual(len(prod.sent), 2)


class TestBackoff(unittest.TestCase):
    def test_doubles_up_to_cap(self):
        seq, b = [], 1
        for _ in range(12):
            seq.append(b)
            b = nc.next_backoff(b)
        self.assertEqual(seq[:5], [1, 2, 4, 8, 16])
        self.assertEqual(max(seq), nc.MAX_BACKOFF)


if __name__ == "__main__":
    unittest.main()
