"""API 테스트: 인증, SSE 실행, 기록·적중률, 헬스체크 계약 (DB·네트워크는 가짜)."""
import hashlib
import hmac
import json
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["APP_PASSWORD"] = "test-pass"

from fastapi.testclient import TestClient  # noqa: E402

import app as appmod  # noqa: E402
import llm as L  # noqa: E402
from fixtures import MemCache, ohlcv, to_cache  # noqa: E402


class FakeDB(MemCache):
    def __init__(self):
        super().__init__()
        self.saved = {}
        self.rows = []

    def ping(self):
        return True

    def save_run(self, run_id, r):
        self.saved[run_id] = r

    def list_runs(self, symbol, limit):
        return [r for r in self.rows if symbol in (None, r["symbol"])][:limit]

    def get_run(self, run_id):
        r = self.saved.get(run_id)
        return None if r is None else {**r, "run_id": run_id}


async def fake_run(symbol, horizon, position, cache, llm, loader=None):
    yield {"type": "step", "step": "data", "status": "running"}
    yield {"type": "step", "step": "data", "status": "done", "detail": "ok"}
    yield {"type": "result", "data": {"symbol": symbol, "name": "X", "horizon": horizon, "quote": {"price": 10.0},
                                      "verdict": {"verdict": "buy", "score": 0.3, "confidence": 0.6},
                                      "meta": {"duration_ms": 5}, "position": position}}


def sse_events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


class TestAPI(unittest.TestCase):
    def setUp(self):
        import asyncio
        self.db = FakeDB()
        appmod.S.db = self.db
        appmod.S.llm = L.ScriptedLLM(lambda *a: {})
        appmod.S.sem = asyncio.Semaphore(2)
        appmod.S.login_fail.clear()
        appmod.pipeline.run = fake_run
        self.c = TestClient(appmod.app)

    def login(self):
        r = self.c.post("/auth/login", json={"password": "test-pass"})
        self.assertEqual(r.status_code, 200)

    def test_health_contract(self):
        r = self.c.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertIn('"status":"ok"', r.text)   # infra/kaspflow-autodeploy.sh 가 이 문자열을 grep 한다

    def test_auth_flow(self):
        self.assertFalse(self.c.get("/auth/me").json()["authenticated"])
        for _ in range(5):
            self.assertEqual(self.c.post("/auth/login", json={"password": "nope"}).status_code, 401)
        self.assertEqual(self.c.post("/auth/login", json={"password": "test-pass"}).status_code, 429)
        appmod.S.login_fail.clear()
        self.login()
        self.assertTrue(self.c.get("/auth/me").json()["authenticated"])
        tok = appmod.make_token()   # 서명 끝 글자 변조 (원래 "0" 이면 바꿔도 같아서 1/16 확률로 통과하던 검사)
        self.assertFalse(appmod.token_ok(tok[:-1] + ("1" if tok[-1] == "0" else "0")))
        self.assertFalse(appmod.token_ok(appmod.make_token(now=0)))   # 만료
        # 비밀번호를 알아도 서버 비밀값 없이는 쿠키를 만들 수 없다 (예전 방식: 비밀번호만으로 만든 키)
        key, exp = hashlib.sha256(b"kaspflow-session:test-pass").digest(), str(int(time.time()) + 3600)
        self.assertFalse(appmod.token_ok(f"{exp}." + hmac.new(key, exp.encode(), hashlib.sha256).hexdigest()))

    def test_analyze_requires_login_and_same_origin(self):
        self.assertEqual(self.c.get("/analyze/AAPL").status_code, 401)
        self.login()
        r = self.c.get("/analyze/AAPL", headers={"sec-fetch-site": "cross-site"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.c.get("/analyze/bad$$").status_code, 400)

    def test_analyze_stream_saves_run(self):
        self.login()
        r = self.c.get("/analyze/aapl?horizon=short&qty=3&avg=9.5")
        self.assertEqual(r.status_code, 200)
        evs = sse_events(r.text)
        self.assertEqual(evs[0]["type"], "start")
        done = evs[-1]
        self.assertEqual(done["type"], "done")
        self.assertIn(done["run_id"], self.db.saved)
        self.assertEqual(self.db.saved[done["run_id"]]["position"], {"qty": 3.0, "avg_price": 9.5})
        self.assertEqual(self.c.get(f"/runs/{done['run_id']}").json()["symbol"], "AAPL")

    def test_runs_track_record(self):
        start = (datetime.now() - timedelta(days=560)).strftime("%Y-%m-%d")
        df = ohlcv(400, seed=1, start=start)
        self.db.set("yf:prices:SYN", to_cache(df))
        old = datetime.now(timezone.utc) - timedelta(days=200)
        eval_px = float(df[df.index >= str((old + timedelta(days=91)).date())]["Close"].iloc[0])
        self.db.rows = [
            {"id": "1", "symbol": "SYN", "name": "S", "created_at": old, "horizon": "mid", "verdict": "buy",
             "score": 0.3, "confidence": 0.6, "price": eval_px / 1.10, "headline": "h"},
            {"id": "2", "symbol": "SYN", "name": "S", "created_at": datetime.now(timezone.utc), "horizon": "mid",
             "verdict": "sell", "score": -0.3, "confidence": 0.6, "price": 10.0, "headline": "h"},
        ]
        appmod.market.last_closes = lambda syms, cache: {"SYN": 11.0}
        body = self.c.get("/runs").json()
        first, second = body["runs"]
        self.assertTrue(first["evaluated"])
        self.assertAlmostEqual(first["return"], 0.10, places=3)
        self.assertTrue(first["hit"])
        self.assertFalse(second["evaluated"])
        self.assertIsNone(second["hit"])
        self.assertAlmostEqual(second["return"], 0.10, places=3)
        self.assertEqual(body["stats"]["evaluated"], 1)

    def test_judge(self):
        self.assertTrue(appmod.judge("buy", 0.03))
        self.assertFalse(appmod.judge("buy", 0.01))
        self.assertTrue(appmod.judge("sell", -0.03))
        self.assertTrue(appmod.judge("hold", 0.04))
        self.assertFalse(appmod.judge("hold", -0.06))


if __name__ == "__main__":
    unittest.main()
