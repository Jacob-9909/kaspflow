"""가중치 자동 조정: 근거 쪽으로 옮기기, 무리 합 유지, 표본 밖 검증, 실제 기록 표본 만들기."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tuning  # noqa: E402
import verdict as vd  # noqa: E402


def synth(n=3000, tech=0.0, reg=0.0, seed=0, h="short"):
    """n 개 표본 (종목 30개 x 날짜). tech/reg 는 그 모듈 신호가 수익률을 설명하는 정도."""
    rng = np.random.default_rng(seed)
    out = []
    start = datetime(2018, 1, 1)
    for i in range(n):
        x = {"technical": rng.normal(), "analogs": rng.normal(), "regime": rng.normal()}
        ret = tech * x["technical"] + reg * x["regime"] + rng.normal()
        d = (start + timedelta(days=31 * (i // 30))).date().isoformat()
        out.append({"h": h, "date": d, "sym": f"S{i % 30}", "x": x, "ret": ret, "src": "bt"})
    return out


class TestTuning(unittest.TestCase):
    def test_moves_toward_evidence_and_keeps_group_total(self):
        prior = vd.WEIGHTS["short"]
        w, ics = tuning.propose(synth(tech=0.3, reg=-0.3), prior, tuning.TESTED, "short", keep_total=True)
        self.assertGreater(w["technical"], prior["technical"])
        self.assertLess(w["regime"], prior["regime"])
        self.assertAlmostEqual(sum(w[m] for m in tuning.TESTED), sum(prior[m] for m in tuning.TESTED), places=2)
        self.assertEqual({m: w[m] for m in prior if m not in tuning.TESTED}, {m: prior[m] for m in prior if m not in tuning.TESTED})
        self.assertGreater(ics["technical"]["ic"], 0.2)

    def test_adopts_only_when_out_of_sample_better(self):
        prior = vd.WEIGHTS["short"]
        good, rep = tuning.tune_horizon(synth(tech=0.4, reg=-0.2, seed=1), "short", prior)
        self.assertTrue(rep["adopted"])
        self.assertGreater(good["technical"], prior["technical"])
        same, rep2 = tuning.tune_horizon(synth(seed=2), "short", prior)     # 신호가 없으면
        self.assertFalse(rep2["adopted"])
        self.assertEqual(same, prior)                                       # 기본값을 그대로 쓴다
        self.assertEqual(rep2["mode"], "backtest")

    def test_live_samples(self):
        t0 = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
        card = lambda k, s, c=1.0: {"key": k, "score": s, "confidence": c, "available": True}
        rows = [{"symbol": "AAPL", "created_at": t0, "price": 100.0, "modules": [card("technical", 0.5), card("valuation", -0.4, 0.5)],
                 "analogs": {"short": {"available": True, "score": 0.2, "confidence": 0.5}}},
                {"symbol": "AAPL", "created_at": t0 + timedelta(hours=1), "price": 101.0, "modules": [card("technical", 0.6)], "analogs": {}}]
        price_on = lambda sym, day: 110.0 if day.date() <= (t0 + timedelta(days=31)).date() else None   # 단기만 기간이 지났다
        s = tuning.live_samples(rows, price_on)
        self.assertEqual(len(s), 1)                    # 같은 종목·같은 날은 마지막 실행만
        self.assertEqual(s[0]["x"], {"technical": 0.6})
        self.assertAlmostEqual(s[0]["ret"], 110 / 101 - 1)
        self.assertEqual(s[0]["src"], "live")
