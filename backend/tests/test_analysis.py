"""재무·밸류에이션 · 뉴스 · 투자 대가 · 판단 엔진 · LLM 클라이언트 · 파이프라인 테스트 (네트워크 없음)."""
import asyncio
import json
import sys
import unittest
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fundamentals as fu  # noqa: E402
import llm as L  # noqa: E402
import news as nw  # noqa: E402
import pipeline  # noqa: E402
import verdict as vd  # noqa: E402
from fixtures import MemCache, analyst, bundle, info, news_items, statements  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class TestFundamentals(unittest.TestCase):
    def setUp(self):
        self.price = 120.0
        self.m = fu.build_metrics(info(self.price), statements(), self.price)

    def test_metrics(self):
        m = self.m
        self.assertAlmostEqual(m["rev_cagr_3y"], (120 / 90) ** (1 / 3) - 1)
        self.assertAlmostEqual(m["debt_to_equity"], 0.318)
        self.assertTrue(m["rev_all_up"])
        self.assertAlmostEqual(m["dividend_yield"], 1.0 / 120)

    def test_quality(self):
        q = fu.quality(self.m)
        self.assertTrue(q["available"])
        self.assertGreater(q["score"], 0.3)
        self.assertGreaterEqual(q["piotroski"]["score"], 6)
        self.assertGreater(q["altman_z"], 2.99)

    def test_dcf_monotonic_and_reverse(self):
        val = fu.valuation(self.m, analyst(self.price), 0.043, 1.1, 90.0, 140.0)
        d = val["dcf"]
        self.assertIsNotNone(d)
        grid = d["sensitivity"]["values"]
        self.assertGreater(grid[0][1], grid[2][1])     # WACC 가 높을수록 가치가 낮다
        self.assertLess(grid[1][0], grid[1][2])        # 영구성장률이 높을수록 가치가 높다
        fcff0 = fu._fcff_base(self.m)
        w = d["wacc"]
        implied = fu.dcf_value(fcff0, d["implied_growth"], w, self.m)["per_share"]
        self.assertAlmostEqual(implied, self.price, delta=0.5)   # 역DCF 성장률을 넣으면 현재가가 나와야 한다
        self.assertTrue(-1 <= val["score"] <= 1)
        self.assertTrue(any(f["method"].startswith("DCF") for f in val["football_field"]))

    def test_consensus(self):
        c = fu.consensus(info(self.price), analyst(self.price), self.price)
        self.assertTrue(c["available"])
        self.assertGreater(c["detail"]["eps_revision_net"], 0)

    def test_etf_without_statements(self):
        m = fu.build_metrics({"quoteType": "ETF"}, None, 50.0)
        self.assertFalse(fu.quality(m)["available"])


class TestNews(unittest.TestCase):
    def test_label_cache_and_aggregate(self):
        items = news_items(6)

        def fake(role, prompt, schema):
            n = prompt.count("\n") and len([ln for ln in prompt.splitlines() if ln[:2].rstrip(".").isdigit()])
            return {"items": [{"i": k + 1, "relevance": 1.0, "sentiment": 0.6 if k % 2 == 0 else -0.2, "impact": "medium",
                               "event": "earnings" if k == 0 else "weird", "title_ko": f"기사 {k}"} for k in range(n)]}

        llm = L.ScriptedLLM(fake)
        cache = MemCache()
        out, err = run(nw.label(items, "SYN", "Synthetic", "Technology", llm, cache))
        self.assertIsNone(err)
        self.assertEqual(len(out), 6)
        self.assertTrue(all("sentiment" in x for x in out))
        self.assertEqual({x["event"] for x in out} - set(nw.EVENTS), set())
        out2, _ = run(nw.label(items, "SYN", "Synthetic", "Technology", llm, cache))
        self.assertEqual(len(llm.calls), 1)   # 두 번째는 캐시
        agg = nw.aggregate(out2)
        self.assertTrue(agg["available"])
        self.assertGreater(agg["score"], 0)

    def test_junk_filter(self):
        import market
        self.assertTrue(market.is_junk("Apple Inc. $AAPL Shares Sold by Sowell Financial Services LLC"))
        self.assertTrue(market.is_junk("NVIDIA Corporation (NVDA) Stock Price, News, Quote & History"))
        self.assertFalse(market.is_junk("Berkshire Hathaway sells its stake in Apple"))
        self.assertFalse(market.is_junk("Apple pays final 2026 dividend of $0.27 per share"))

    def test_label_without_llm_keeps_items(self):
        out, err = run(nw.label(news_items(3), "SYN", "S", None, L.NimLLM([]), MemCache()))
        self.assertEqual(len(out), 3)
        self.assertIsNotNone(err)
        self.assertFalse(nw.aggregate(out)["available"])


class TestVerdict(unittest.TestCase):
    def mods(self, s):
        return {k: {"available": True, "score": s, "confidence": 1.0, "summary": k} for k in vd.MODULES}

    def test_thresholds(self):
        risk = {"vol_1y": 0.3}
        self.assertEqual(vd.combine(self.mods(0.5), "mid", risk, None)["verdict"], "buy")
        self.assertEqual(vd.combine(self.mods(-0.5), "mid", risk, None)["verdict"], "sell")
        self.assertEqual(vd.combine(self.mods(0.05), "mid", risk, None)["verdict"], "hold")
        for h, w in vd.WEIGHTS.items():
            self.assertAlmostEqual(sum(w.values()), 1.0, places=6, msg=h)

    def test_low_confidence_shrinks_to_hold(self):
        m = self.mods(0.6)
        for x in m.values():
            x["confidence"] = 0.1
        self.assertEqual(vd.combine(m, "mid", {"vol_1y": 0.2}, None)["verdict"], "hold")

    def test_earnings_lowers_confidence(self):
        a = vd.combine(self.mods(0.5), "short", {"vol_1y": 0.2}, None)["confidence"]
        b = vd.combine(self.mods(0.5), "short", {"vol_1y": 0.2}, 3)["confidence"]
        self.assertLess(b, a)

    def test_tidy_summary_drops_other_verdicts(self):
        s = "적정가가 현재가를 크게 웃돌아 매도 판단이 섭니다. 재무 건전성은 반대 근거입니다. 200일선 회복을 지켜봅니다."
        self.assertEqual(vd.tidy_summary(s, "관망"), "재무 건전성은 반대 근거입니다. 200일선 회복을 지켜봅니다.")
        self.assertEqual(vd.tidy_summary("관망 판단입니다. 근거가 엇갈립니다.", "관망"), "관망 판단입니다. 근거가 엇갈립니다.")
        self.assertEqual(vd.tidy_summary("매수 판단이 우세합니다.", "관망"), "매수 판단이 우세합니다.")  # 다 빠지면 원문

    def test_noun_end(self):
        cases = {"추세가 약합니다.": "추세가 약함", "하락 위험이 큽니다": "하락 위험이 큼", "보유 신호를 보입니다": "보유 신호를 보임",
                 "변동성이 높습니다": "변동성이 높음", "매도 쪽으로 바뀝니다": "매도 쪽으로 바뀜", "데드크로스 상태입니다": "데드크로스 상태임",
                 "중기 추세가 우상향": "중기 추세가 우상향", "부채가 적어 금리 부담이 작음": "부채가 적어 금리 부담이 작음"}
        for a, b in cases.items():
            self.assertEqual(vd.noun_end(a), b)


class TestNimClient(unittest.TestCase):
    def test_retry_fallback_and_parse(self):
        seen = []

        def handler(req: httpx.Request):
            body = json.loads(req.content)
            seen.append(body["model"])
            if body["model"] == "primary":
                return httpx.Response(410, json={"error": "gone"})
            content = '<think>x</think>```json\n{"headline": "h", "summary": "s", "bull": [], "bear": [], "risks": [], "triggers": []}\n```'
            return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})

        c = L.NimLLM(["k1", "k2"], model="primary", fallback="backup", rpm=0, transport=httpx.MockTransport(handler))
        out = run(c.generate("cio", "sys", "prompt", vd.Narrative))
        self.assertEqual(out.headline, "h")
        self.assertEqual(seen[0], "primary")
        self.assertEqual(seen[-1], "backup")

    def test_disabled(self):
        with self.assertRaises(L.LLMUnavailable):
            run(L.NimLLM([]).generate("x", "s", "p", vd.Narrative))


def scripted(role, prompt, schema):
    if role == "news":
        n = len([ln for ln in prompt.splitlines() if ln[:2].rstrip(".").isdigit()])
        return {"items": [{"i": k + 1, "relevance": 0.9, "sentiment": 0.3, "impact": "high", "event": "earnings",
                           "title_ko": "제목"} for k in range(n)]}
    if role.startswith("persona"):
        return {"signal": "bullish", "confidence": 70, "thesis": "좋다", "positives": ["a"], "negatives": ["b"]}
    if role == "cio":
        return {"headline": "요약", "summary": "설명", "bull": ["b1"], "bear": ["r1"], "risks": ["x"], "triggers": ["t"]}
    raise AssertionError(role)


class TestPipeline(unittest.TestCase):
    def collect(self, llm, quote_type="EQUITY", horizon="mid"):
        b = bundle(quote_type=quote_type)
        events = []

        async def go():
            async for ev in pipeline.run("SYN", horizon, {"qty": 10, "avg_price": 50.0}, MemCache(), llm,
                                         loader=lambda s, c: b):
                events.append(ev)

        run(go())
        return events

    def test_full_run_with_llm(self):
        llm = L.ScriptedLLM(scripted)
        events = self.collect(llm)
        steps = [e["step"] for e in events if e["type"] == "step" and e["status"] == "done"]
        self.assertEqual(steps, [s for s, _ in pipeline.STEPS])
        res = events[-1]["data"]
        self.assertEqual(events[-1]["type"], "result")
        self.assertIn(res["verdict"]["verdict"], ("buy", "hold", "sell"))
        self.assertEqual(set(res["verdicts"]), {"short", "mid", "long"})
        self.assertEqual(res["narrative"]["headline"], "요약")
        self.assertEqual(llm.calls.count("cio"), 1)
        self.assertEqual(len([c for c in llm.calls if c.startswith("persona")]), 6)
        self.assertTrue(res["advice"]["holding"])
        json.dumps(res)  # 직렬화 가능해야 저장할 수 있다
        self.assertTrue(res["news"]["aggregate"]["available"])

    def test_llm_failures_fall_back(self):
        llm = L.ScriptedLLM(lambda role, p, s: RuntimeError("boom"))
        res = self.collect(llm)[-1]["data"]
        self.assertTrue(res["narrative"]["summary"].startswith("AI 해설"))
        self.assertIsNotNone(res["meta"]["narrative_error"])
        self.assertTrue(all(m["view"] is None for m in res["personas"]["members"].values()))

    def test_etf(self):
        res = self.collect(L.NimLLM([]), quote_type="ETF", horizon="long")[-1]["data"]
        cards = {c["key"]: c for c in res["modules"]}
        self.assertFalse(cards["valuation"]["available"])
        self.assertTrue(cards["technical"]["available"])
        self.assertEqual(list(res["personas"]["members"]), ["druckenmiller"])


if __name__ == "__main__":
    unittest.main()
