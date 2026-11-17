"""판단 엔진 과거 검증: 미래 값을 쓰지 않는지(시점 정합성)와 요약 형식."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import engine_backtest as eb  # noqa: E402
from fixtures import ohlcv  # noqa: E402


class TestEngineBacktest(unittest.TestCase):
    def test_no_lookahead(self):
        df, spy = ohlcv(1300, seed=3), ohlcv(1300, drift=0.0003, vol=0.01, seed=4)["Close"]
        vix, tnx = spy * 0 + 20, spy * 0 + 4
        a = eb.evaluate(df, spy, vix, tnx, None, None, step=63, start=504)
        later = df.copy()
        later.iloc[900:, :4] *= 3          # 900번째 봉 뒤의 가격을 크게 바꿔도
        b = eb.evaluate(later, spy, vix, tnx, None, None, step=63, start=504)
        cut = str(df.index[900].date())
        pick = lambda rows: [(x["h"], x["date"], x["verdict"], round(x["conf"], 6)) for x in rows if x["date"] < cut]
        self.assertTrue(pick(a))
        self.assertEqual(pick(a), pick(b))  # 그 전 시점의 판단은 그대로여야 한다 (이후 수익률만 바뀐다)

    def test_summary_shape(self):
        rows = [{"h": "short", "date": "2020-01-01", "verdict": v, "conf": c, "ret": r}
                for v, c, r in [("buy", 0.9, 0.05), ("buy", 0.7, -0.01), ("sell", 0.5, -0.04), ("hold", 0.65, 0.01)]]
        s = eb.summarize(rows)["short"]
        self.assertEqual(s["n"], 4)
        self.assertEqual(s["by_verdict"]["buy"], {"n": 2, "hit": 0.5, "base": 0.25, "avg": 0.02})
        self.assertEqual([c["n"] for c in s["by_conf"]], [1, 2, 1])
