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

    def list_runs(self, symbol, limit, source=None):
        keep = lambda r: source is None or (r.get("source") == "auto") == (source == "auto")
        return [r for r in self.rows if symbol in (None, r["symbol"]) and keep(r)][:limit]

    def watch_overview(self):
        return [{"symbol": k, "horizon": h, "run_id": None, "created_at": None, "name": None, "price": None, "change": None,
                 "headline": None, "action": None, "verdicts": {}, "modules": []} for k, h in self.watch.items()]

    def get_run(self, run_id):
        r = self.saved.get(run_id)
        return None if r is None else {**r, "run_id": run_id}

    watch: dict = {}

    def list_watchlist(self):
        return [{"symbol": k, "horizon": h, "added_at": datetime(2026, 11, 17, tzinfo=timezone.utc), "name": None,
                 "last": None, "prev": None} for k, h in self.watch.items()]

    def add_watch(self, symbol, horizon):
        self.watch[symbol] = horizon

    def remove_watch(self, symbol):
        self.watch.pop(symbol, None)

    def weights_history(self, n=6):
        return []

    def save_weights(self, weights, adopted, report):
        self.saved_weights = (weights, adopted, report)
        return 7


async def fake_run(symbol, horizon, position, cache, llm, loader=None, weights=None):
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
        body = r.json()
        self.assertEqual(body["version"], "dev")              # 이미지를 만든 커밋 (테스트에서는 GIT_SHA 가 없다)
        self.assertIn("next_at", body["autorun"])
        self.assertEqual(body["tuning"]["running"], False)

    def test_security_headers_and_docs_off(self):
        r = self.c.get("/health")
        for k, v in appmod.SECURITY_HEADERS.items():
            self.assertEqual(r.headers.get(k), v)
        self.assertNotIn("strict-transport-security", r.headers)     # 평문 http 에는 HSTS 를 붙이지 않는다
        self.assertIn("strict-transport-security", TestClient(appmod.app, base_url="https://testserver").get("/health").headers)
        self.assertEqual(self.c.get("/docs").status_code, 404)        # 문서 화면은 API_DOCS=1 일 때만
        self.assertEqual(self.c.get("/openapi.json").status_code, 404)

    def test_public_routes_are_throttled_and_validated(self):
        self.assertEqual(self.c.get("/runs?symbol=$$").status_code, 400)   # 예전에는 500
        real, appmod.market.quote = appmod.market.quote, lambda sym, cache: {"symbol": sym}
        limit, appmod.PUBLIC_RATE = appmod.PUBLIC_RATE, (3, 60.0)
        appmod.S.rate.clear()
        try:
            codes = [self.c.get("/quote/SYN").status_code for _ in range(4)]
        finally:
            appmod.market.quote, appmod.PUBLIC_RATE = real, limit
            appmod.S.rate.clear()
        self.assertEqual(codes, [200, 200, 200, 429])

    def test_autorun_once_records_failures(self):
        import asyncio
        self.db.watch = {"OK": "mid", "BAD": "mid"}

        async def run(symbol, horizon, position, cache, llm, loader=None, weights=None):
            if symbol == "BAD":
                raise RuntimeError("boom")
            async for ev in fake_run(symbol, horizon, position, cache, llm):
                yield ev
        appmod.pipeline.run = run
        try:
            failed = asyncio.run(appmod._autorun_once())
        finally:
            self.db.watch = {}
        self.assertEqual(failed, ["BAD"])
        self.assertEqual([r["symbol"] for r in self.db.saved.values()], ["OK"])
        self.assertEqual(self.c.get("/health").json()["autorun"]["failed"], ["BAD"])
        self.assertIn("BAD", self.c.get("/health").json()["last_error"])

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

    def test_runs_source_filter(self):
        now = datetime.now(timezone.utc)
        row = lambda i, src: {"id": i, "symbol": "SYN", "name": "S", "created_at": now, "horizon": "mid", "verdict": "buy",
                              "score": 0.3, "confidence": 0.6, "price": 10.0, "headline": "h", "source": src}
        self.db.rows = [row("1", "auto"), row("2", None)]
        appmod.market.last_closes = lambda syms, cache: {"SYN": 11.0}
        ids = lambda q: [r["id"] for r in self.c.get("/runs" + q).json()["runs"]]
        self.assertEqual((ids(""), ids("?source=auto"), ids("?source=manual")), (["1", "2"], ["1"], ["2"]))
        self.assertEqual(self.c.get("/runs?source=x").status_code, 422)

    def test_market_overview_from_cache(self):
        import overview
        start = (datetime.now() - timedelta(days=420)).strftime("%Y-%m-%d")
        for i, (sym, _) in enumerate(overview.INDEXES + overview.ASSETS + overview.SECTORS):
            self.db.set(f"yf:series:{sym}:{'10y' if sym == 'SPY' else '2y'}", to_cache(ohlcv(300, seed=i, start=start)))
        self.db.set("yf:series:^VIX:2y", to_cache(ohlcv(300, seed=90, start=start)))
        self.db.set("yf:series:^TNX:2y", to_cache(ohlcv(300, seed=91, start=start)))
        body = self.c.get("/market").json()
        self.assertEqual((len(body["indexes"]), len(body["assets"]), len(body["sectors"])), (4, 4, 11))
        spy = body["indexes"][0]
        self.assertEqual(spy["symbol"], "SPY")
        self.assertTrue(set(("price", "d1", "w1", "m1", "m3", "ytd", "vs_200", "spark")) <= set(spy))
        self.assertEqual(spy["spark"][-1], spy["price"])            # 흐름 선의 끝점은 오늘 가격
        self.assertTrue(body["regime"]["available"])
        self.assertEqual(body["breadth"]["total"], 11)
        self.assertIn(body["vix"]["word"], ("안정", "보통", "높음", "매우 높음"))
        self.assertIn("market:overview", self.db.d)                 # 묶은 결과를 캐시한다

    def test_market_leaders_from_cache(self):
        import overview
        start = (datetime.now() - timedelta(days=420)).strftime("%Y-%m-%d")
        for i, (etf, _) in enumerate(overview.SECTORS):
            syms = [f"S{i}X{j}" for j in range(overview.LEADERS_N)]
            self.db.set(f"yf:holdings:{etf}", [{"symbol": s, "name": s, "weight": 0.1} for s in syms])
            for j, s in enumerate(syms):
                self.db.set(f"yf:series:{s}:2y", to_cache(ohlcv(300, seed=100 + i * 10 + j, start=start)))
                self.db.set(f"yf:info:{s}", {"shortName": f"{s} Inc.", "marketCap": 1e12, "trailingPE": 30.0, "forwardPE": 25.0, "targetMeanPrice": 1e6})
        body = self.c.get("/market/leaders").json()
        self.assertEqual(len(body["sectors"]), 11)
        first = body["sectors"][0]
        self.assertEqual((first["symbol"], len(first["leaders"])), ("XLK", 5))
        row = first["leaders"][0]
        self.assertTrue(set(("symbol", "name", "price", "d1", "w1", "m1", "vs_200", "from_high", "pe", "forward_pe", "target_upside")) <= set(row))
        self.assertEqual(row["name"], "S0X0")                       # 회사명 꼬리(Inc.)는 뗀다
        self.assertGreater(row["target_upside"], 0)
        self.assertIn("market:leaders", self.db.d)                  # 묶은 결과를 캐시한다

    def test_leaders_keep_one_share_class_per_company(self):
        import overview
        rows = [{"symbol": s, "name": n, "weight": 0.1} for s, n in
                [("META", "Meta Platforms Inc Class A"), ("GOOGL", "Alphabet Inc Class A"), ("GOOG", "Alphabet Inc Class C"), ("DIS", "Walt Disney Co")]]
        self.assertEqual([r["symbol"] for r in overview.top_unique(rows, 3)], ["META", "GOOGL", "DIS"])

    def test_market_leaders_unavailable(self):
        import overview
        real = overview._holdings
        overview._holdings = lambda etf: None                       # 보유 종목을 못 받으면 503
        try:
            r = self.c.get("/market/leaders")
        finally:
            overview._holdings = real
        self.assertEqual(r.status_code, 503)

    def test_market_overview_unavailable(self):
        real, appmod.market._history = appmod.market._history, lambda sym, period: None   # 시세를 못 받으면 503 과 안내 문장
        try:
            r = self.c.get("/market")
        finally:
            appmod.market._history = real
        self.assertEqual(r.status_code, 503)
        self.assertIn("시장 데이터", r.json()["detail"])

    def test_watchlist_overview(self):
        self.db.watch = {"NVDA": "mid"}
        body = self.c.get("/watchlist/overview").json()
        self.assertEqual(body["items"][0]["symbol"], "NVDA")
        self.db.watch = {}

    def test_watchlist_requires_login_and_limit(self):
        self.db.watch = {}
        self.assertEqual(self.c.post("/watchlist", json={"symbol": "AAPL"}).status_code, 401)
        self.login()
        self.assertEqual(self.c.post("/watchlist", json={"symbol": "aapl", "horizon": "long"}).status_code, 200)
        items = self.c.get("/watchlist").json()["items"]
        self.assertEqual([(w["symbol"], w["horizon"]) for w in items], [("AAPL", "long")])
        self.assertEqual(self.c.post("/watchlist", json={"symbol": "MSFT", "horizon": "week"}).status_code, 400)
        for i in range(appmod.WATCH_MAX - 1):
            self.db.add_watch(f"T{i}", "mid")
        self.assertEqual(self.c.post("/watchlist", json={"symbol": "NVDA"}).status_code, 400)   # 최대 개수
        self.assertEqual(self.c.post("/watchlist", json={"symbol": "AAPL", "horizon": "mid"}).status_code, 200)  # 이미 있는 종목은 바꾸기
        self.assertEqual(self.c.delete("/watchlist/AAPL").status_code, 200)
        self.assertNotIn("AAPL", self.db.watch)

    def test_autorun_slots_and_due(self):
        ny = appmod.NY
        fri_eve = datetime(2026, 11, 20, 17, 0, tzinfo=ny)      # 금요일 장 마감 뒤
        self.assertEqual(appmod._slot(fri_eve, -1), datetime(2026, 11, 20, 16, 40, tzinfo=ny))
        self.assertEqual(appmod._slot(fri_eve, 1), datetime(2026, 11, 23, 16, 40, tzinfo=ny))   # 다음은 월요일
        sun = datetime(2026, 11, 22, 12, 0, tzinfo=ny)
        self.assertEqual(appmod._slot(sun, -1), datetime(2026, 11, 20, 16, 40, tzinfo=ny))     # 주말엔 금요일
        mon_am = datetime(2026, 11, 23, 9, 0, tzinfo=ny)
        self.assertEqual(appmod._slot(mon_am, -1), datetime(2026, 11, 20, 16, 40, tzinfo=ny))
        old = {"symbol": "A", "last": {"created_at": datetime(2026, 11, 20, 12, 0, tzinfo=ny)}}
        new = {"symbol": "B", "last": {"created_at": datetime(2026, 11, 20, 16, 50, tzinfo=ny)}}
        none = {"symbol": "C", "last": None}
        self.assertEqual([w["symbol"] for w in appmod.autorun_due([old, new, none], sun)], ["A", "C"])

    def test_weights_api(self):
        appmod.S.weights = None
        r = self.c.get("/weights").json()
        self.assertEqual(r["current"], r["defaults"])
        self.assertIsNone(r["version"])
        self.assertEqual(self.c.post("/weights/tune").status_code, 401)
        self.assertEqual(self.c.post("/weights/reset").status_code, 401)
        self.login()
        self.assertEqual(self.c.post("/weights/reset").status_code, 200)
        self.assertEqual(appmod.S.weights["version"], 7)
        self.assertEqual(self.db.saved_weights[2], {"reset": True})

    def test_effective_weights_follow_code_defaults(self):
        old = {h: {**w, "regime": 0.99} for h, w in appmod.vd.WEIGHTS.items()}     # 예전 기본값이 저장돼 있다고 치고
        row = {"weights": old, "report": {"horizons": {"short": {"adopted": True}, "mid": {"adopted": False}}}}
        w = appmod.effective_weights(row)
        self.assertEqual(w["short"]["regime"], 0.99)                                # 채택된 기간은 저장된 값
        self.assertEqual(w["mid"], appmod.vd.WEIGHTS["mid"])                        # 채택 안 된 기간은 지금 기본값
        self.assertEqual(w["long"], appmod.vd.WEIGHTS["long"])                      # 보고서에 없는 기간도
        self.assertEqual(appmod.effective_weights({"weights": old, "report": {"reset": True}}), appmod.vd.WEIGHTS)

    def test_judge(self):
        self.assertTrue(appmod.judge("buy", 0.03))
        self.assertFalse(appmod.judge("buy", 0.01))
        self.assertTrue(appmod.judge("sell", -0.03))
        self.assertTrue(appmod.judge("hold", 0.04))
        self.assertFalse(appmod.judge("hold", -0.06))


if __name__ == "__main__":
    unittest.main()
