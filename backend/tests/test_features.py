"""
/features 의 순수 함수 테스트 (DB 불필요): 지표가 NaN 이어도 JSON 직렬화가 되고, 뉴스 열이 붙는다.

실행:  cd backend && python -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import features as ft  # noqa: E402
import news_features as nf  # noqa: E402

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def candles(n=40):
    rng = np.random.default_rng(3)
    closes = 100 + np.cumsum(rng.normal(0, 1, n))
    return [{"window_start": T0 + timedelta(minutes=15 * i), "open": c, "high": c + 1, "low": c - 1,
             "close": c, "volume": 10.0 + i} for i, c in enumerate(closes)]


class TestFeatureRows(unittest.TestCase):
    def rows(self, news=()):
        cs = candles()
        times = [int(c["window_start"].timestamp()) for c in cs]
        feats = nf.news_features(times, 15, list(news), nf.NEWS_DEFAULT_PARAMS)
        return ft.build_feature_rows(cs, feats)

    def test_columns_and_warmup_nans_become_none(self):
        rows = self.rows()
        self.assertEqual(len(rows), 40)
        self.assertIsNone(rows[0]["ret_1"])
        self.assertIsNone(rows[0]["sma_15"])
        self.assertIsNotNone(rows[-1]["sma_15"])
        for name in nf.EVENT_TYPE_NAMES:
            self.assertEqual(rows[0][f"event_{name}"], 0)
        self.assertEqual((rows[0]["news_score"], rows[0]["news_weight"], rows[0]["news_count"]), (0.0, 0.0, 0))

    def test_json_strict(self):
        json.dumps(self.rows(), default=str, allow_nan=False)

    def test_news_columns_filled(self):
        c = T0 + timedelta(minutes=15 * 5, seconds=100)
        news = [{"symbol": "BTCUSDT", "collected_at": c, "published_at": c - timedelta(minutes=5),
                 "p_relevant": 0.9, "p_pos": 0.8, "p_neg": 0.1, "event_type": "macro"}]
        rows = self.rows(news)
        self.assertEqual(rows[5]["news_count"], 1)
        self.assertEqual(rows[5]["event_macro"], 1)
        self.assertGreater(rows[5]["news_score"], 0)
        self.assertEqual(rows[4]["news_count"], 0)

    def test_indicators_match_strategy_definitions(self):
        cs = candles()
        rows = self.rows()
        closes = np.array([c["close"] for c in cs])
        self.assertAlmostEqual(rows[20]["sma_3"], closes[18:21].mean(), places=9)
        self.assertAlmostEqual(rows[20]["sma_15"], closes[6:21].mean(), places=9)
        self.assertAlmostEqual(rows[20]["ret_1"], closes[20] / closes[19] - 1, places=9)


if __name__ == "__main__":
    unittest.main()
