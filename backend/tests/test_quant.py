"""지표 · 전략 · 백테스트 엔진 · 검증기 · 시뮬레이션 단위 테스트 (네트워크·DB 불필요)."""
import math
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backtest as bt  # noqa: E402
import indicators as ta  # noqa: E402
import simulation as sim  # noqa: E402
import strategies as st  # noqa: E402
import validation as va  # noqa: E402
from fixtures import ohlcv  # noqa: E402


def tiny(o, h, l, c):
    idx = pd.bdate_range("2024-01-01", periods=len(c))
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": [1.0] * len(c)}, index=idx, dtype=float)


class TestIndicators(unittest.TestCase):
    def test_rsi_bounds_and_trend(self):
        up = pd.Series(np.linspace(1, 2, 50))
        self.assertAlmostEqual(ta.rsi(up).iloc[-1], 100.0)
        df = ohlcv(300)
        r = ta.rsi(df["Close"]).dropna()
        self.assertTrue(((r >= 0) & (r <= 100)).all())

    def test_atr_positive(self):
        df = ohlcv(100)
        self.assertTrue((ta.atr(df.High, df.Low, df.Close).dropna() > 0).all())


class TestStrategies(unittest.TestCase):
    def test_no_lookahead(self):
        """앞부분만으로 계산한 신호와 전체로 계산한 신호의 앞부분이 같아야 한다 (미래 참조 없음)."""
        df = ohlcv(900, seed=3)
        for s in st.STRATEGIES:
            p = s.grid[0]
            full = s.fn(df, p)[0]
            for k in (400, 650):
                part = s.fn(df.iloc[:k], p)[0]
                np.testing.assert_allclose(part.to_numpy(float), full.iloc[:k].to_numpy(float), err_msg=s.key, equal_nan=True)

    def test_positions_are_binary(self):
        df = ohlcv(600)
        for s in st.STRATEGIES:
            if s.kind == "position":
                vals = set(np.unique(s.fn(df, s.grid[0])[0].to_numpy()))
                self.assertTrue(vals <= {0.0, 1.0}, s.key)
        self.assertEqual(st.n_variants(), 30)


class TestEngine(unittest.TestCase):
    def test_next_open_fills(self):
        df = tiny([10, 11, 12, 13], [11, 12, 13, 14], [9, 10, 11, 12], [10.5, 11.5, 12.5, 13.5])
        pos = pd.Series([1, 1, 0, 0], index=df.index, dtype=float)
        r, held = bt.position_returns(df, pos, np.zeros(4), 0.0)
        self.assertAlmostEqual(float(np.prod(1 + r)), 13 / 11)   # 1일 시가 11 매수 -> 3일 시가 13 매도
        np.testing.assert_array_equal(held, [0, 1, 1, 0])
        tr = bt.extract_trades(df, held, 0.0)
        self.assertEqual(len(tr), 1)
        self.assertAlmostEqual(tr[0]["ret"], 13 / 11 - 1)
        fee = 0.001
        r2, _ = bt.position_returns(df, pos, np.zeros(4), fee)
        self.assertLess(np.prod(1 + r2), np.prod(1 + r))
        self.assertAlmostEqual(bt.extract_trades(df, held, fee)[0]["ret"], 13 * (1 - fee) / (11 * (1 + fee)) - 1)

    def test_cash_earns_rf(self):
        df = tiny([10] * 5, [10] * 5, [10] * 5, [10] * 5)
        r, _ = bt.position_returns(df, pd.Series(0.0, index=df.index), np.full(5, 0.001), 0.0)
        self.assertAlmostEqual(float(r[1:].sum()), 0.004)

    def test_breakout(self):
        df = tiny([10, 10, 10, 10], [11, 11, 10.2, 11], [9, 9, 9.9, 9], [10.5, 10.8, 10, 10.5])
        level = st._vol_breakout(df, {"k": 0.5})[0]
        r, held, trades = bt.breakout_returns(df, level, np.zeros(4), 0.0)
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0]["ret"], 10 / 11 - 1)
        self.assertAlmostEqual(float(np.prod(1 + r)), 10 / 11)

    def test_metrics_sane(self):
        df = ohlcv(800)
        res = bt.buy_hold(df, np.zeros(len(df)))
        m = bt.metrics(res["returns"], np.zeros(len(df)), res["held"], res["trades"])
        self.assertLessEqual(m["max_drawdown"], 0)
        self.assertAlmostEqual(m["total_return"], df.Open.iloc[-1] / df.Open.iloc[1] * df.Close.iloc[-1] / df.Open.iloc[-1] - 1,
                               places=3)


class TestValidation(unittest.TestCase):
    def test_folds(self):
        f = va._folds(2600)
        self.assertGreater(len(f), 3)
        for (a0, a, b), nxt in zip(f, f[1:]):
            self.assertEqual(b, nxt[1])          # OOS 구간이 이어진다
            self.assertEqual(a - a0, va.IS_DAYS)
        self.assertEqual(len(va._folds(600)), 1)
        self.assertEqual(va._folds(200), [])
        self.assertGreater(len(va._folds(3650, 365)), 3)

    def test_psr_dsr(self):
        rng = np.random.default_rng(0)
        r = rng.normal(0.001, 0.01, 1500)
        self.assertGreater(va.psr(r, np.zeros(1500)), 0.95)
        self.assertLess(va.psr(-r, np.zeros(1500)), 0.05)
        self.assertLess(va.expected_max_sr(0.0004, 5), va.expected_max_sr(0.0004, 50))

    def test_random_walk_mostly_fails(self):
        df = ohlcv(2600, drift=0.0, seed=11)
        v = va.validate_all(df, np.zeros(len(df)))
        grades = [s["grade"] for s in v["strategies"]]
        self.assertLessEqual(grades.count("A"), 1, grades)
        self.assertEqual(v["n_trials"], 30)
        for s in v["strategies"]:
            if s["perm_p"] is not None:
                self.assertTrue(0 < s["perm_p"] <= 1)

    def test_trending_series_has_timing_skill(self):
        df = ohlcv(2600, drift=0.0, vol=0.01, seed=5, ar=0.25)
        v = va.validate_all(df, np.zeros(len(df)))
        best = min(s["perm_p"] for s in v["strategies"] if s["family"] == "trend" and s["perm_p"] is not None)
        self.assertLess(best, 0.05)
        sc = va.strategy_score(v)
        self.assertGreater(sc["confidence"], 0)


class TestSimulation(unittest.TestCase):
    def test_paths_and_plan(self):
        df = ohlcv(1000, seed=2)
        close = df["Close"].to_numpy()
        paths = sim.forward_paths(close, 0.08)
        self.assertEqual(paths.shape, (2000, 252))
        med = float(np.median(paths[:, -1] / close[-1] - 1))
        self.assertLess(abs(med - 0.08), 0.12)
        summary = sim.summarize_paths(paths, close[-1])
        self.assertLessEqual(summary["mid"]["p5"], summary["mid"]["median"])
        atr = float(ta.atr(df.High, df.Low, df.Close).iloc[-1])
        plan = sim.trade_plan(close[-1], atr, paths, "mid")
        self.assertLess(plan["stop"], plan["entry"])
        self.assertGreater(plan["target"], plan["entry"])
        self.assertAlmostEqual(plan["p_target_first"] + plan["p_stop_first"] + plan["p_neither"], 1.0, places=2)
        self.assertLessEqual(plan["max_weight"], sim.MAX_WEIGHT)

    def test_analogs_and_risk(self):
        df = ohlcv(2600, seed=4)
        a = sim.analogs(df, "short")
        self.assertIn("available", a)
        if a["available"]:
            self.assertTrue(-1 <= a["score"] <= 1)
        spy = ohlcv(2600, seed=9)["Close"]
        rp = sim.risk_profile(df, spy)
        self.assertGreater(rp["vol_1y"], 0)
        self.assertFalse(math.isnan(rp["atr"]))


if __name__ == "__main__":
    unittest.main()
