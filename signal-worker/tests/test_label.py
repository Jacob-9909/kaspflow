"""label.py 지표 함수 테스트 (DB 없음). 실행: cd signal-worker && python -m unittest discover -s tests -v"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import label as lb  # noqa: E402


class MetricsTest(unittest.TestCase):
    def test_accuracy_and_baseline(self):
        probs = [[0.9, 0.1], [0.2, 0.8], [0.6, 0.4], [0.3, 0.7]]
        y = [0, 1, 1, 1]
        self.assertEqual(lb.accuracy(probs, y), 0.75)
        self.assertEqual(lb.majority_baseline(y), 0.75)

    def test_ece_perfect_and_overconfident(self):
        # 신뢰도 1.0 에 전부 정답 -> 0, 신뢰도 0.9 에 절반 정답 -> 0.4
        self.assertAlmostEqual(lb.ece([[1.0, 0.0]] * 4, [0] * 4), 0.0)
        self.assertAlmostEqual(lb.ece([[0.9, 0.1]] * 4, [0, 0, 1, 1]), 0.4)

    def test_temperature_softens_overconfidence(self):
        probs = [[0.95, 0.05]] * 20
        y = [0] * 12 + [1] * 8  # 실제 정답률 0.6
        t = lb.fit_temperature(probs, y)
        self.assertGreater(t, 1.0)
        self.assertAlmostEqual(lb.scale([0.95, 0.05], t)[0], 0.6, delta=0.05)
        self.assertLess(lb.ece([lb.scale(p, t) for p in probs], y), lb.ece(probs, y))

    def test_gate_sentiment_needs_margin_over_baseline(self):
        r = {"field": "sentiment", "acc": 0.75, "baseline": 0.70, "ece_cal": 0.05}
        self.assertFalse(lb.gate(r))
        self.assertTrue(lb.gate({**r, "baseline": 0.60}))
        self.assertFalse(lb.gate({**r, "baseline": 0.60, "ece_cal": None}))

    def test_build_sets_maps_labels(self):
        row = {"p_relevant": 0.75, "p_pos": 0.1, "p_neu": 0.2, "p_neg": 0.7,
               "p_event": {"hack_security": 0.8, "other": 0.2},
               "relevance": "mention", "sentiment": "negative", "event_type": "hack_security"}
        s = lb.build_sets([row])
        self.assertEqual(s["relevance"], ([[0.75, 0.25]], [1]))
        self.assertEqual(s["sentiment"][1], [2])
        self.assertEqual(s["event_type"][1], [lb.EVENT_ORDER.index("hack_security")])


if __name__ == "__main__":
    unittest.main()
