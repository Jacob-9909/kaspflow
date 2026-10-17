"""
뉴스 API 테스트 (실제 PostgreSQL 필요)
============================================================
TEST_DATABASE_URL 이 없으면(또는 httpx 가 없으면) 건너뛴다. 빈 DB 에 db/init.sql, db/news.sql 을 적용하고
ohlc_1m / 뉴스 신호를 직접 넣은 뒤 FastAPI TestClient 로 엔드포인트를 호출한다.

실행 예) TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
         python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_URL = os.getenv("TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]

try:
    import httpx  # noqa: F401  (fastapi TestClient 의존성)
    HAVE_CLIENT = True
except ImportError:  # pragma: no cover
    HAVE_CLIENT = False

MV = "test@1/schema1"
SYMBOL = "BTCUSDT"


@unittest.skipUnless(DB_URL and HAVE_CLIENT, "TEST_DATABASE_URL 또는 httpx 가 없어 건너뜁니다")
class TestNewsApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from fastapi.testclient import TestClient
        from psycopg_pool import ConnectionPool

        import app

        cls.app = app
        cls.psycopg = psycopg
        cls.sql = {n: (ROOT / "db" / n).read_text(encoding="utf-8") for n in ("init.sql", "news.sql")}
        with psycopg.connect(DB_URL, autocommit=True) as c:
            c.execute(cls.sql["init.sql"])
            c.execute(cls.sql["news.sql"])
        app.pool = ConnectionPool(DB_URL, min_size=1, max_size=3, open=True)
        cls.client = TestClient(app.app)
        # 8시간치 1분봉: 15분 경계에 맞춘 시작 시각
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        cls.base = (now - timedelta(hours=8)).replace(minute=((now - timedelta(hours=8)).minute // 15) * 15)

    @classmethod
    def tearDownClass(cls):
        cls.app.pool.close()

    def setUp(self):
        with self.psycopg.connect(DB_URL, autocommit=True) as c:
            c.execute("TRUNCATE news_label, news_signal, news_raw, ohlc_1m")
            for i in range(8 * 60):
                c.execute(
                    "INSERT INTO ohlc_1m (symbol, window_start, open, high, low, close, volume, trade_count) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 10, 5)",
                    (SYMBOL, self.base + timedelta(minutes=i), 101.0, 102.0, 99.0, 100.0 + i * 0.01),
                )

    # -- 헬퍼 --
    def add_signal(self, aid, collected_at, symbol=SYMBOL, mv=MV, rel=0.9, pos=0.8, neu=0.1, neg=0.1,
                   event="regulation_legal", title=None, created_offset=0):
        with self.psycopg.connect(DB_URL, autocommit=True) as c:
            c.execute(
                "INSERT INTO news_raw (article_id, source, url, title, body, lang, published_at, collected_at) "
                "VALUES (%s, 'coindesk', %s, %s, '', 'en', %s, %s) ON CONFLICT DO NOTHING",
                (aid, f"https://x.test/{aid}", title or f"title {aid}", collected_at - timedelta(minutes=10), collected_at),
            )
            c.execute(
                "INSERT INTO news_signal (article_id, symbol, model_version, collected_at, p_relevant, p_pos, p_neu, p_neg,"
                " event_type, p_event, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'{}'::jsonb, now() + %s * interval '1 second')",
                (aid, symbol, mv, collected_at, rel, pos, neu, neg, event, created_offset),
            )

    def bar(self, k, offset=100):
        return self.base + timedelta(minutes=15 * k, seconds=offset)

    def seed_positive(self, k=5, n=2):
        for j in range(n):
            self.add_signal(f"p{k}-{j}", self.bar(k, 100 + 10 * j))

    # -- /news/health --
    def test_health_empty_ok_and_stale(self):
        self.assertEqual(self.client.get("/news/health").json()["status"], "empty")
        self.add_signal("old", datetime.now(timezone.utc) - timedelta(hours=2))
        h = self.client.get("/news/health").json()
        self.assertEqual(h["status"], "stale")
        self.assertGreater(h["age_minutes"], 100)
        self.add_signal("fresh", datetime.now(timezone.utc) - timedelta(minutes=1))
        h = self.client.get("/news/health").json()
        self.assertEqual(h["status"], "ok")
        self.assertLess(h["age_minutes"], 5)
        self.assertEqual((h["articles_24h"], h["signals_24h"], h["model_version"]), (2, 2, MV))

    # -- /news --
    def test_news_list_newest_first_with_sentiment_and_filters(self):
        self.add_signal("a1", self.bar(1), title="Older", pos=0.1, neu=0.1, neg=0.8)
        self.add_signal("a2", self.bar(2), title="Newer", pos=0.8, neu=0.1, neg=0.1)
        self.add_signal("a3", self.bar(3), symbol="ETHUSDT", title="Eth")
        body = self.client.get("/news", params={"symbol": SYMBOL}).json()
        self.assertEqual([n["title"] for n in body["news"]], ["Newer", "Older"])
        self.assertEqual([n["sentiment"] for n in body["news"]], ["positive", "negative"])
        self.assertEqual(body["news"][0]["url"], "https://x.test/a2")
        self.assertEqual(len(self.client.get("/news").json()["news"]), 3)
        self.assertEqual(len(self.client.get("/news", params={"limit": 1}).json()["news"]), 1)
        since = self.bar(2).isoformat()
        self.assertEqual(len(self.client.get("/news", params={"symbol": SYMBOL, "since": since}).json()["news"]), 1)

    def test_only_latest_model_version_is_used_by_default(self):
        self.add_signal("a1", self.bar(1), mv="old@1/schema1", title="old model", created_offset=-60)
        self.add_signal("a1", self.bar(1), mv="new@2/schema1", title="x", created_offset=0)
        news = self.client.get("/news", params={"symbol": SYMBOL}).json()["news"]
        self.assertEqual([n["model_version"] for n in news], ["new@2/schema1"])
        pinned = self.client.get("/news", params={"symbol": SYMBOL, "model_version": "old@1/schema1"}).json()["news"]
        self.assertEqual([n["model_version"] for n in pinned], ["old@1/schema1"])

    # -- /news/score, /features --
    def test_score_series_follows_candles_and_boundaries(self):
        self.seed_positive(5)
        body = self.client.get("/news/score", params={"symbol": SYMBOL, "interval": "15m"}).json()
        series = {s["time"]: s for s in body["series"]}
        t = lambda k: int((self.base + timedelta(minutes=15 * k)).timestamp())  # noqa: E731
        self.assertEqual(series[t(4)]["count"], 0)       # 봉 5 마감 전에는 기사가 없다
        self.assertEqual(series[t(5)]["count"], 2)       # 봉 5 안에서 수집 -> 봉 5 에 들어간다
        self.assertGreater(series[t(5)]["score"], 0.3)
        self.assertGreater(series[t(6)]["count"], 0)     # lookback 안이라 이후 봉에도 남는다
        self.assertLess(series[t(6)]["weight"], series[t(5)]["weight"])  # 감쇠
        self.assertEqual(body["model_version"], MV)

    def test_features_json_and_csv(self):
        self.seed_positive(5)
        body = self.client.get("/features", params={"symbol": SYMBOL, "interval": "15m"}).json()
        rows = body["rows"]
        self.assertIsNone(rows[0]["ret_1"])                       # 첫 봉은 수익률이 없다
        self.assertIsNotNone(rows[-1]["rsi_14"])
        self.assertIn("event_macro", rows[0])
        self.assertTrue(any(r["event_regulation_legal"] > 0 for r in rows))
        self.assertTrue(any(r["news_score"] > 0 for r in rows))
        csv = self.client.get("/features", params={"symbol": SYMBOL, "interval": "15m", "format": "csv"})
        self.assertEqual(csv.headers["content-type"].split(";")[0], "text/csv")
        header = csv.text.splitlines()[0].split(",")
        for col in ("window_start", "close", "news_score", "news_weight", "event_other"):
            self.assertIn(col, header)

    # -- /backtest --
    def test_backtest_news_sentiment_uses_news_and_defaults_to_next_open(self):
        self.seed_positive(5)
        res = self.client.get("/backtest", params={
            "symbol": SYMBOL, "interval": "15m", "strategy": "news_sentiment", "fee_bps": 0, "compare": "true",
        }).json()
        self.assertEqual(res["fill"], "next_open")
        self.assertEqual(res["news"]["model_version"], MV)
        self.assertEqual(res["news"]["articles"], 2)
        buy = next(m for m in res["markers"] if m["side"] == "buy")
        self.assertEqual(buy["time"], int((self.base + timedelta(minutes=15 * 6)).timestamp()))  # 봉 5 판단 -> 봉 6 시가
        self.assertEqual(buy["price"], 101.0)
        self.assertEqual(set(res["baselines"]), {"combined", "buy_hold"})

    def test_backtest_rejects_bad_news_params_and_lists_strategy(self):
        r = self.client.get("/backtest", params={"symbol": SYMBOL, "interval": "15m", "strategy": "news_sentiment",
                                                 "params": '{"buy_th": 5}'})
        self.assertEqual(r.status_code, 400)
        strategies = self.client.get("/backtest/strategies").json()["strategies"]
        by_id = {s["id"]: s for s in strategies}
        self.assertTrue(by_id["news_sentiment"]["requires_news"])
        self.assertFalse(by_id["macd"]["requires_news"])
        self.assertEqual(by_id["news_sentiment"]["params"]["buy_th"], 0.3)

    def test_backtest_without_any_news_warns(self):
        res = self.client.get("/backtest", params={"symbol": SYMBOL, "interval": "15m", "strategy": "news_sentiment"}).json()
        self.assertEqual(res["trades"], [])
        self.assertTrue(any("뉴스 신호가 없습니다" in w for w in res["warnings"]))

    def test_30m_interval(self):
        res = self.client.get("/backtest", params={"symbol": SYMBOL, "interval": "30m", "strategy": "macd"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["interval_minutes"], 30)
        self.assertIn("30m", self.client.get("/intervals").json()["intervals"])

    # -- 뉴스 테이블이 없을 때 --
    def test_missing_news_tables_only_break_news_endpoints(self):
        with self.psycopg.connect(DB_URL, autocommit=True) as c:
            c.execute("DROP TABLE news_label, news_signal, news_raw")
        try:
            for path, params in (("/news", {}), ("/news/health", {}), ("/news/score", {"symbol": SYMBOL}),
                                 ("/features", {"symbol": SYMBOL}),
                                 ("/backtest", {"symbol": SYMBOL, "interval": "15m", "strategy": "news_sentiment"})):
                with self.subTest(path=path):
                    r = self.client.get(path, params=params)
                    self.assertEqual(r.status_code, 503)
                    self.assertIn("db/news.sql", r.json()["detail"])
            # 시세와 기존 전략은 그대로 동작한다
            self.assertEqual(self.client.get("/health").status_code, 200)
            self.assertEqual(self.client.get("/backtest", params={"symbol": SYMBOL, "interval": "15m", "strategy": "macd"}).status_code, 200)
        finally:
            with self.psycopg.connect(DB_URL, autocommit=True) as c:
                c.execute(self.sql["news.sql"])


if __name__ == "__main__":
    unittest.main()
