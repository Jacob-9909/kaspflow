"""실제 PostgreSQL 통합 테스트. TEST_DATABASE_URL 이 있을 때만 돈다 (CI 는 postgres:17 서비스로 돌린다)."""
import os
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import DB  # noqa: E402

URL = os.environ.get("TEST_DATABASE_URL")


@unittest.skipUnless(URL, "TEST_DATABASE_URL 이 없어 건너뜀")
class TestDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = DB(URL)
        cls.db.pool.open(wait=True)
        cls.db.init_schema()
        cls.db.init_schema()  # 두 번 돌려도 같아야 한다 (기동할 때마다 실행)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_cache_ttl(self):
        key = f"test:{uuid.uuid4()}"
        self.assertIsNone(self.db.get(key, 60))
        self.db.set(key, {"a": [1, 2]})
        self.assertEqual(self.db.get(key, 60), {"a": [1, 2]})
        self.db.set(key, {"a": 3})
        self.assertEqual(self.db.get(key, 60), {"a": 3})
        with self.db.pool.connection() as c:
            c.execute("UPDATE stock.cache SET fetched_at = now() - interval '2 hours' WHERE key = %s", (key,))
        self.assertIsNone(self.db.get(key, 3600))
        self.assertTrue(self.db.ping())

    def test_runs(self):
        rid = str(uuid.uuid4())
        result = {"symbol": "TST", "name": "Test", "horizon": "mid", "quote": {"price": 12.5},
                  "verdict": {"verdict": "hold", "score": 0.01, "confidence": 0.4},
                  "narrative": {"headline": "관망"}, "meta": {"duration_ms": 1234}}
        self.db.save_run(rid, result)
        rows = self.db.list_runs("TST", 10)
        self.assertEqual(str(rows[0]["id"]), rid)
        self.assertEqual(rows[0]["headline"], "관망")
        got = self.db.get_run(rid)
        self.assertEqual(got["verdict"]["verdict"], "hold")
        self.assertIsNone(self.db.get_run(str(uuid.uuid4())))


if __name__ == "__main__":
    unittest.main()
