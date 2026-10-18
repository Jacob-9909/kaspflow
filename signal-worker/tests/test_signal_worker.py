"""
signal-worker 테스트
============================================================
- 매칭/질문/응답 파싱: 외부 의존 없음
- 저장·분류 흐름(process_message, backfill): 실제 PostgreSQL 이 필요하다.
    TEST_DATABASE_URL 이 없으면 이 부분만 건너뛴다. (db/news.sql 을 직접 적용하므로 빈 DB 면 된다)
    예) TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres
- Laya 모델은 쓰지 않는다 (가짜 분류기로 대체).

실행:  cd signal-worker && python -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import classifier as clf  # noqa: E402
import main as worker  # noqa: E402
import matching as mt  # noqa: E402
import questions as qs  # noqa: E402
import store as st  # noqa: E402

BOOK = mt.load_book()
DB_URL = os.getenv("TEST_DATABASE_URL")
NEWS_SQL = Path(__file__).resolve().parents[2] / "db" / "news.sql"


class TestMatching(unittest.TestCase):
    def m(self, text):
        return mt.match_symbols(text, BOOK)

    def test_ticker_and_name_aliases(self):
        self.assertEqual(self.m("BTC jumps as Ethereum lags"), ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(self.m("Ripple wins; XRP rallies"), ["XRPUSDT"])
        self.assertEqual(self.m("bitcoin and ETHER"), ["BTCUSDT", "ETHUSDT"])

    def test_ticker_is_case_sensitive_but_names_are_not(self):
        self.assertEqual(self.m("the btc market"), [])           # 소문자 티커는 무시
        self.assertEqual(self.m("the BITCOIN market"), ["BTCUSDT"])  # 이름은 대소문자 무관

    def test_word_boundaries(self):
        for text in ("BTCUSD perp", "xBTC token", "ETHEREAL vibes", "method ETHos", "WETHx"):
            with self.subTest(text=text):
                self.assertEqual(self.m(text), [])
        for text in ("BTC-USD", "(BTC)", "BTC,", "BTC.", "BTC/USDT", "Bitcoin's price"):
            with self.subTest(text=text):
                self.assertEqual(self.m(text), ["BTCUSDT"])

    def test_exclusions(self):
        self.assertEqual(self.m("Bitcoin Cash hard fork"), [])
        self.assertEqual(self.m("Bitcoin Cash and Bitcoin"), ["BTCUSDT"])
        self.assertEqual(self.m("Ethereum Classic upgrade"), [])

    def test_no_match(self):
        self.assertEqual(self.m("Solana copycat soars"), [])

    def test_names_for_prompts(self):
        self.assertEqual(BOOK.name_of("XRPUSDT"), "XRP")
        self.assertEqual(BOOK.name_of("MARKET"), "the overall crypto market")
        with self.assertRaises(KeyError):
            BOOK.name_of("DOGEUSDT")

    def test_bad_config(self):
        with self.assertRaises(ValueError):
            mt.build_book({"symbols": {"X": {"aliases": []}}})


class TestQuestions(unittest.TestCase):
    def test_all_choice_with_small_option_sets(self):
        for qset in (qs.coin_questions("Bitcoin (BTC)"), qs.market_questions("the overall crypto market")):
            for key, q in qset.items():
                with self.subTest(question=key):
                    self.assertEqual(q["type"], "choice")  # noul/score 는 쓰지 않는다
                    self.assertLessEqual(len(q["criteria"]), 8)
                    self.assertTrue(all(len(v) <= 60 for v in q["criteria"].values()))  # 토큰 예산 보호

    def test_coin_name_is_in_instructions(self):
        q = qs.coin_questions("XRP")
        self.assertEqual(q["relevance"]["criteria"][qs.RELEVANCE_MAIN], "XRP")  # schema 2: 종목은 선택지 A
        self.assertIn("XRP", q["sentiment"]["instructions"])

    def test_event_keys_cover_options(self):
        self.assertEqual(set(qs.EVENT_TYPES), set(qs.coin_questions("x")["event_type"]["criteria"]))
        self.assertEqual(set(qs.SENTIMENT_KEYS), set(qs.coin_questions("x")["sentiment"]["criteria"]))


def laya_answers(first="relevance", p_first=0.9, sent=(0.7, 0.2, 0.1), event="B"):
    ev = {k: 0.02 for k in qs.EVENT_TYPES}
    ev[event] = 0.88
    return {
        first: {"probabilities": {"A": p_first, "B": round(1 - p_first, 4)}},
        "sentiment": {"probabilities": dict(zip("ABC", sent))},
        "event_type": {"probabilities": ev},
    }


class TestParseAnswers(unittest.TestCase):
    def test_coin(self):
        c = clf.parse_answers(laya_answers(), "relevance")
        self.assertAlmostEqual(c.p_relevant, 0.9)
        self.assertEqual((c.p_pos, c.p_neu, c.p_neg), (0.7, 0.2, 0.1))
        self.assertEqual(c.event_type, "regulation_legal")
        self.assertEqual(set(c.p_event), set(qs.EVENT_TYPES.values()))

    def test_market_uses_scope_probability(self):
        c = clf.parse_answers(laya_answers("scope", 0.95), "scope")
        self.assertAlmostEqual(c.p_relevant, 0.95)

    def test_missing_probabilities_raise(self):
        bad = laya_answers()
        del bad["sentiment"]["probabilities"]["C"]
        with self.assertRaises(ValueError):
            clf.parse_answers(bad, "relevance")

    def test_build_text(self):
        self.assertEqual(clf.build_text(" T ", None), "T")
        self.assertEqual(clf.build_text("T", " body "), "T\n\nbody")
        self.assertEqual(len(clf.build_text("T", "x" * 9999)), clf.MAX_TEXT_CHARS)


class TestParseMessage(unittest.TestCase):
    def test_ok_and_timestamps(self):
        m = st.parse_message(json.dumps(msg("a1", "Bitcoin up")))
        self.assertEqual(m["collected_at"], datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(m["published_at"], datetime(2026, 10, 16, 11, 0, tzinfo=timezone.utc))

    def test_null_published_and_empty_lang(self):
        m = st.parse_message(json.dumps(msg("a1", "t", published_at=None, lang="")))
        self.assertIsNone(m["published_at"])
        self.assertIsNone(m["lang"])

    def test_rejects_bad_messages(self):
        for bad in (b"not json", b"[]", json.dumps({"article_id": "x"}), json.dumps(msg("a", "t", collected_at="nope"))):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                st.parse_message(bad)


def msg(aid, title, body="", collected_at="2026-10-16T12:00:00Z", published_at="2026-10-16T11:00:00Z", lang="en"):
    return {"article_id": aid, "source": "coindesk", "url": f"https://x.test/{aid}", "title": title,
            "body": body, "lang": lang, "published_at": published_at, "collected_at": collected_at}


class FakeClassifier:
    """Laya 대역. fail_once 가 켜지면 첫 호출에서 예외를 낸다. market=True 면 시장 전체 기사로 판정한다."""

    def __init__(self, version="fake@1/schema1", market=False, fail_once=False):
        self.model_version = version
        self.market = market
        self.fail_once = fail_once
        self.calls = []

    def classify_coin(self, text, name):
        self.calls.append(("coin", name))
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("model crashed")
        return clf.Classification(0.9, 0.6, 0.3, 0.1, "regulation_legal",
                                  {v: (0.8 if v == "regulation_legal" else 0.2 / 6) for v in qs.EVENT_TYPES.values()})

    def classify_market(self, text, name):
        self.calls.append(("market", name))
        if not self.market:
            return None
        return clf.Classification(0.95, 0.1, 0.2, 0.7, "macro", {"macro": 1.0})


@unittest.skipUnless(DB_URL, "TEST_DATABASE_URL 이 없어 DB 테스트를 건너뜁니다")
class TestWithPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        with psycopg.connect(DB_URL, autocommit=True) as c:
            c.execute(NEWS_SQL.read_text(encoding="utf-8"))

    def setUp(self):
        self.store = st.PgStore(DB_URL)
        with self.store.conn.cursor() as cur:
            cur.execute("TRUNCATE news_label, news_signal, news_raw")
        self.store.commit()

    def tearDown(self):
        self.store.close()

    def rows(self, sql, *args):
        with self.store.conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()

    def run_msg(self, m, classifier):
        status = worker.process_message(json.dumps(m), self.store, BOOK, classifier)
        return status

    def test_store_only_inserts_once_and_keeps_first_collected_at(self):
        self.assertEqual(self.run_msg(msg("a1", "Bitcoin up"), None), "stored")
        again = msg("a1", "Bitcoin up (재수집)", collected_at="2026-10-16T13:00:00Z")
        self.assertEqual(self.run_msg(again, None), "duplicate")
        rows = self.rows("SELECT title, collected_at FROM news_raw")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "Bitcoin up")
        self.assertEqual(rows[0][1], datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(self.rows("SELECT count(*) FROM news_signal")[0][0], 0)

    def test_classifies_each_matched_symbol(self):
        c = FakeClassifier()
        self.assertEqual(self.run_msg(msg("a1", "BTC and Ethereum rally"), c), "classified")
        rows = self.rows("SELECT symbol, model_version, p_relevant, event_type, p_event FROM news_signal ORDER BY symbol")
        self.assertEqual([r[0] for r in rows], ["BTCUSDT", "ETHUSDT"])
        self.assertEqual({r[1] for r in rows}, {"fake@1/schema1"})
        self.assertEqual(rows[0][3], "regulation_legal")
        self.assertAlmostEqual(rows[0][4]["regulation_legal"], 0.8)  # JSONB 로 전체 분포 저장
        self.assertEqual(c.calls, [("coin", "Bitcoin (BTC)"), ("coin", "Ethereum (ETH)")])

    def test_signal_collected_at_is_copied_from_news_raw(self):
        c = FakeClassifier()
        self.run_msg(msg("a1", "BTC up"), c)
        # 같은 기사가 나중 시각으로 재수집되고 새 모델이 처리해도, 신호의 collected_at 은 최초 값이어야 한다
        later = msg("a1", "BTC up", collected_at="2026-10-16T15:00:00Z")
        self.run_msg(later, FakeClassifier(version="fake@2/schema1"))
        times = {r[0] for r in self.rows("SELECT collected_at FROM news_signal")}
        self.assertEqual(times, {datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)})
        self.assertEqual(len(self.rows("SELECT 1 FROM news_signal")), 2)  # 모델 버전별로 별도 행

    def test_redelivery_is_idempotent(self):
        c = FakeClassifier()
        self.assertEqual(self.run_msg(msg("a1", "BTC up"), c), "classified")
        self.assertEqual(self.run_msg(msg("a1", "BTC up"), c), "duplicate")
        self.assertEqual(len(self.rows("SELECT 1 FROM news_signal")), 1)
        self.assertEqual(len(c.calls), 1)  # 두 번째는 모델을 다시 부르지 않는다

    def test_classifier_failure_keeps_raw_and_redelivery_finishes_the_job(self):
        c = FakeClassifier(fail_once=True)
        with self.assertRaises(RuntimeError):
            self.run_msg(msg("a1", "BTC up"), c)
        self.store.rollback()
        self.assertEqual(len(self.rows("SELECT 1 FROM news_raw")), 1)      # 원문은 남아 있다
        self.assertEqual(len(self.rows("SELECT 1 FROM news_signal")), 0)
        self.assertEqual(self.run_msg(msg("a1", "BTC up"), c), "classified")  # 재전달 때 분류가 끝난다
        self.assertEqual(len(self.rows("SELECT 1 FROM news_signal")), 1)

    def test_unmatched_article_is_dropped_unless_market_wide(self):
        no = FakeClassifier(market=False)
        self.assertEqual(self.run_msg(msg("a1", "Solana copycat soars"), no), "unmatched")
        self.assertEqual(no.calls, [("market", "the overall crypto market")])
        self.assertEqual(len(self.rows("SELECT 1 FROM news_signal")), 0)

        yes = FakeClassifier(market=True)
        self.assertEqual(self.run_msg(msg("a2", "Crypto market slides on Fed fears"), yes), "classified")
        rows = self.rows("SELECT symbol, p_relevant, event_type FROM news_signal")
        self.assertEqual(rows, [("MARKET", 0.95, "macro")])

    def test_backfill_classifies_stored_articles_once(self):
        self.run_msg(msg("a1", "BTC up"), None)
        self.run_msg(msg("a2", "Ripple wins"), None)
        self.run_msg(msg("a3", "Solana copycat"), None)
        c = FakeClassifier()
        done = worker.run_backfill(self.store, BOOK, c, batch=2)
        self.assertEqual(done, {"classified": 2, "unmatched": 1, "failed": 0})
        self.assertEqual(sorted(r[0] for r in self.rows("SELECT symbol FROM news_signal")), ["BTCUSDT", "XRPUSDT"])
        # 이미 신호가 있는 기사는 다시 처리하지 않는다 (매칭 없는 기사만 다시 나온다)
        again = worker.run_backfill(self.store, BOOK, c, batch=2)
        self.assertEqual(again["classified"], 0)

    def test_bad_message_raises_value_error(self):
        with self.assertRaises(ValueError):
            worker.process_message(b"garbage", self.store, BOOK, None)

    def test_null_published_at_is_allowed(self):
        self.assertEqual(self.run_msg(msg("a1", "BTC up", published_at=None), None), "stored")
        self.assertIsNone(self.rows("SELECT published_at FROM news_raw")[0][0])


if __name__ == "__main__":
    unittest.main()
