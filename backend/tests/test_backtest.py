"""
백테스트 엔진 테스트 (DB/네트워크 불필요)
============================================================
midas-touch 의 tests/test_backtest.py(리스크 오버레이 회귀 테스트)를 코인용 시그니처에 맞춰 이식하고,
소수 수량·마커·검증 테스트를 더했다.

실행:  cd backend && python -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest as bt  # noqa: E402

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def candles_from(closes, volume=1000.0, step_min=1):
    """종가 배열로 봉 리스트를 만든다 (high=low=open=close)."""
    return [
        {
            "window_start": T0 + timedelta(minutes=step_min * i),
            "open": c, "high": c, "low": c, "close": c, "volume": volume,
        }
        for i, c in enumerate(closes)
    ]


def sim(closes, position, capital=1_000_000.0, risk=None, sizing=None):
    """simulate() 를 간단히 호출한다 (times 는 봉 인덱스)."""
    return bt.simulate(
        np.array(closes, dtype=float),
        np.array(position, dtype=float),
        list(range(len(closes))),
        capital,
        bt.merge_risk(risk or {"fee_bps": 0}),
        bt.merge_sizing(sizing),
    )


class TestRiskOverlay(unittest.TestCase):
    """midas-touch test_backtest.py 이식"""

    def test_stop_loss_caps_loss_and_no_immediate_reentry(self):
        # i=1 진입(px=100) -> i=2 px=90 (-10%) 로 -8% 손절 발동
        out = sim([100, 100, 90, 95, 99], [0, 1, 1, 1, 1], risk={"stop_loss_pct": 0.08, "fee_bps": 0})
        self.assertEqual(len(out["trades"]), 1)
        self.assertEqual(out["trades"][0]["exit_reason"], "stop_loss")
        self.assertLessEqual(out["trades"][0]["pnl_pct"], -0.08)
        # 손절 후 스탠스가 계속 1 이어도 재진입하지 않는다 -> 마지막엔 현금
        self.assertIsNone(out["open_position"])
        self.assertEqual(out["in_market"][-1], 0.0)

    def test_trailing_stop(self):
        # 고점 130 후 110 으로 -15% -> -12% 추격 손절
        out = sim([100, 100, 120, 130, 110], [0, 1, 1, 1, 1], risk={"trailing_stop_pct": 0.12, "fee_bps": 0})
        self.assertTrue(out["trades"])
        self.assertEqual(out["trades"][-1]["exit_reason"], "trailing_stop")

    def test_take_profit(self):
        out = sim([100, 100, 110, 125, 130], [0, 1, 1, 1, 1], risk={"take_profit_pct": 0.2, "fee_bps": 0})
        self.assertEqual(out["trades"][0]["exit_reason"], "take_profit")
        self.assertGreaterEqual(out["trades"][0]["pnl_pct"], 0.2)

    def test_fees_reduce_final_value(self):
        pos = [0, 1, 1]
        no_fee = sim([100, 100, 110], pos, risk={"fee_bps": 0})
        with_fee = sim([100, 100, 110], pos, risk={"fee_bps": 100})
        self.assertLess(with_fee["equity"][-1], no_fee["equity"][-1])

    def test_signal_exit_without_risk(self):
        out = sim([100, 100, 110, 120], [0, 1, 1, -1], risk={"fee_bps": 0})
        self.assertEqual(len(out["trades"]), 1)
        self.assertEqual(out["trades"][0]["exit_reason"], "signal")
        self.assertGreater(out["trades"][0]["pnl_pct"], 0)

    def test_combined_actually_trades_on_trend(self):
        # 평탄 구간 뒤 상승 -> 복합 전략이 진입해야 한다
        closes = [100.0] * 25 + [100.0 + i for i in range(1, 41)]
        df = pd.DataFrame({"Close": closes, "High": closes, "Low": closes, "Volume": [1000.0] * len(closes)})
        pos = bt.compute_position(df, "combined", {})
        self.assertTrue((pos == 1).any(), "상승 추세에서 combined 가 매수 스탠스를 내야 함")


class TestCryptoAdjustments(unittest.TestCase):
    def test_fractional_quantity_all_in(self):
        # 자본 1,000 USDT 로 50,000 짜리 코인을 산다 -> 0.02 개 (주식처럼 0주가 되면 안 됨)
        out = sim([50_000, 50_000, 55_000, 60_000], [0, 1, 1, -1], capital=1_000.0, risk={"fee_bps": 0})
        trade = out["trades"][0]
        self.assertAlmostEqual(trade["qty"], 0.02, places=8)
        self.assertAlmostEqual(out["equity"][-1], 1_200.0, places=6)

    def test_open_position_reported_and_marked_to_market(self):
        out = sim([100, 100, 110], [0, 1, 1], risk={"fee_bps": 0})
        self.assertEqual(out["trades"], [])
        self.assertIsNotNone(out["open_position"])
        self.assertAlmostEqual(out["open_position"]["unrealized_pct"], 0.10, places=6)
        self.assertAlmostEqual(out["equity"][-1], 1_100_000.0, places=4)


class TestRunBacktest(unittest.TestCase):
    CLOSES = [10, 10, 10, 10, 10, 11, 12, 13, 12, 10, 8, 6, 6, 6]
    SMA = {"short_window": 2, "long_window": 4}

    def run_sma(self, **kw):
        return bt.run_backtest(
            candles_from(self.CLOSES), "sma_crossover", params=self.SMA,
            risk={"fee_bps": 0}, **kw,
        )

    def test_drops_unconfirmed_last_bar(self):
        res = self.run_sma()
        self.assertEqual(res["bars"], len(self.CLOSES) - 1)
        kept = bt.run_backtest(candles_from(self.CLOSES), "sma_crossover", params=self.SMA,
                               risk={"fee_bps": 0}, drop_last_bar=False)
        self.assertEqual(kept["bars"], len(self.CLOSES))

    def test_sma_markers_alternate_buy_then_sell(self):
        res = self.run_sma()
        sides = [m["side"] for m in res["markers"]]
        self.assertGreaterEqual(len(sides), 2)
        self.assertEqual(sides[0], "buy")
        for a, b in zip(sides, sides[1:]):
            self.assertNotEqual(a, b, "매수/매도는 번갈아 나와야 함 (롱 온리, 포지션 1개)")
        # 마커 시각은 입력 봉의 시각(epoch 초)과 일치해야 차트에 찍힌다
        valid_times = {int((T0 + timedelta(minutes=i)).timestamp()) for i in range(len(self.CLOSES))}
        self.assertTrue(all(m["time"] in valid_times for m in res["markers"]))
        self.assertEqual(res["metrics"]["trade_count"], len(res["trades"]))

    def test_metrics_are_strict_json(self):
        res = self.run_sma()
        json.dumps(res, allow_nan=False)  # NaN/inf 가 섞이면 예외

    def test_flat_market_warns_and_has_no_trades(self):
        res = bt.run_backtest(candles_from([100.0] * 60), "macd")
        self.assertEqual(res["trades"], [])
        self.assertEqual(res["metrics"]["total_return"], 0.0)
        self.assertTrue(any("체결" in w for w in res["warnings"]))
        json.dumps(res, allow_nan=False)

    def test_short_history_warns(self):
        res = bt.run_backtest(candles_from([10, 11, 12, 11, 10, 9, 10, 11]), "sma_crossover")
        self.assertTrue(any("워밍업" in w for w in res["warnings"]))

    def test_every_strategy_runs_on_noisy_series(self):
        rng = np.random.default_rng(7)
        closes = list(100 + np.cumsum(rng.normal(0, 1, 400)))
        for sid in bt.STRATEGY_LABELS:
            with self.subTest(strategy=sid):
                res = bt.run_backtest(candles_from(closes, step_min=5), sid, interval_minutes=5)
                json.dumps(res, allow_nan=False)
                self.assertEqual(res["bars"], 399)
                self.assertIn("sharpe_ratio", res["metrics"])


class TestSizing(unittest.TestCase):
    """부분 매수(buy_pct) / 분할 매도(sell_tranches)"""

    def test_defaults_are_all_in_single_sell(self):
        out = sim([100, 100, 110, 120], [0, 1, 1, -1], capital=1000.0)
        self.assertEqual(out["trades"][0]["legs"], 1)
        self.assertAlmostEqual(out["trades"][0]["qty"], 10.0)
        self.assertAlmostEqual(out["equity"][-1], 1200.0)

    def test_partial_buy_invests_only_fraction_and_keeps_cash(self):
        out = sim([100, 100, 110, 120], [0, 1, 1, -1], capital=1000.0, sizing={"buy_pct": 0.5})
        t = out["trades"][0]
        self.assertAlmostEqual(t["qty"], 5.0)                   # 현금 500 / 100
        self.assertAlmostEqual(t["pnl_pct"], 0.2)               # 투입금 500 -> 600
        self.assertAlmostEqual(out["equity"][-1], 1100.0)       # 남겨 둔 현금 500 + 600
        self.assertAlmostEqual(out["exposure"][2], 5 * 110 / (500 + 550), places=6)  # 투입 비중

    def test_split_sell_in_equal_tranches_on_consecutive_bars(self):
        out = sim([100, 100, 110, 120, 130, 140, 150], [0, 1, 1, -1, -1, -1, -1],
                  capital=900.0, sizing={"sell_tranches": 3})
        sells = [e for e in out["events"] if e["side"] == "sell"]
        self.assertEqual([e["time"] for e in sells], [3, 4, 5])               # 연속 3개 봉
        self.assertEqual([e["leg"] for e in sells], [1, 2, 3])
        self.assertEqual([e["final"] for e in sells], [False, False, True])
        self.assertEqual(len(out["trades"]), 1)                               # 거래는 1건으로 묶임
        t = out["trades"][0]
        self.assertEqual(t["legs"], 3)
        self.assertAlmostEqual(t["exit_price"], 130.0)                        # 평균 청산가
        self.assertAlmostEqual(t["pnl_pct"], 0.3)                             # (360+390+420)/900 - 1
        self.assertAlmostEqual(out["equity"][-1], 900 * 1.3)
        self.assertIsNone(out["open_position"])

    def test_risk_exit_during_split_sell_liquidates_remainder_at_once(self):
        out = sim([100, 100, 110, 120, 90, 80], [0, 1, 1, -1, -1, -1], capital=900.0,
                  risk={"stop_loss_pct": 0.08, "fee_bps": 0}, sizing={"sell_tranches": 3})
        sells = [e for e in out["events"] if e["side"] == "sell"]
        self.assertEqual([e["reason"] for e in sells], ["signal", "stop_loss"])
        self.assertTrue(sells[-1]["final"])
        self.assertEqual(out["trades"][0]["exit_reason"], "stop_loss")
        self.assertEqual(out["trades"][0]["legs"], 2)
        self.assertIsNone(out["open_position"])

    def test_split_sell_cancelled_when_stance_returns_to_buy(self):
        out = sim([100, 100, 110, 120, 125, 130, 140], [0, 1, 1, -1, 1, 1, 1], capital=900.0,
                  sizing={"sell_tranches": 3})
        self.assertEqual(len([e for e in out["events"] if e["side"] == "sell"]), 1)   # 1/3 만 팔고 중단
        self.assertEqual(out["trades"], [])
        self.assertIsNotNone(out["open_position"])
        self.assertAlmostEqual(out["open_position"]["qty"], 6.0)                      # 9 - 3
        self.assertEqual(out["open_position"]["sold_legs"], 1)

    def test_leg_labels_restart_for_each_sell_signal(self):
        # 1/3 매도 후 취소 -> 나중에 새 매도 신호: 라벨은 다시 1/3, 2/3, 3/3 (누적 4/3 이 되면 안 됨)
        out = sim([100, 100, 110, 120, 125, 130, 140, 150, 160, 170], [0, 1, 1, -1, 1, 1, 1, -1, -1, -1],
                  capital=900.0, sizing={"sell_tranches": 3})
        sells = [e for e in out["events"] if e["side"] == "sell"]
        self.assertEqual([e["leg"] for e in sells], [1, 1, 2, 3])
        self.assertEqual([e["legs_total"] for e in sells], [3, 3, 3, 3])
        self.assertEqual(out["trades"][0]["legs"], 4)                       # 거래 전체 매도 횟수는 누적
        self.assertAlmostEqual(out["trades"][0]["qty"], 9.0)
        self.assertAlmostEqual(out["equity"][-1], 3 * 120 + 2 * 150 + 2 * 160 + 2 * 170, places=6)  # 1,320

    def test_position_accounting_invariants_on_random_series(self):
        rng = np.random.default_rng(3)
        closes = list(100 + np.cumsum(rng.normal(0, 1, 600)))
        df = pd.DataFrame({"Close": closes, "High": closes, "Low": closes, "Volume": [1000.0] * 600})
        pos = bt.compute_position(df, "sma_crossover", {"short_window": 3, "long_window": 15}).to_numpy(float)
        for sizing in ({"buy_pct": 0.4, "sell_tranches": 4}, {"buy_pct": 1.0, "sell_tranches": 5}, {"sell_tranches": 2}):
            with self.subTest(sizing=sizing):
                out = sim(closes, pos, capital=10_000.0, risk={"fee_bps": 10, "stop_loss_pct": 0.03}, sizing=sizing)
                self.assertTrue((out["equity"] > 0).all())
                # 마커 상태머신: buy -> sell(여러 번) -> 마지막 sell 이 final, 매수 중복/선매도 없음
                holding = False
                for e in out["events"]:
                    if e["side"] == "buy":
                        self.assertFalse(holding, "보유 중 추가 매수 금지")
                        holding = True
                    else:
                        self.assertTrue(holding, "보유 없이 매도 금지")
                        if e["final"]:
                            holding = False
                self.assertEqual(holding, out["open_position"] is not None)
                self.assertTrue(all(t["legs"] >= 1 and t["qty"] > 0 for t in out["trades"]))

    def test_run_backtest_reports_sizing_and_legs(self):
        closes = [10] * 5 + [11, 12, 13, 14, 15, 14, 12, 10, 8, 6, 6, 6, 6]
        res = bt.run_backtest(candles_from(closes), "sma_crossover", params={"short_window": 2, "long_window": 4},
                              risk={"fee_bps": 0}, sizing={"buy_pct": 0.5, "sell_tranches": 2}, drop_last_bar=False)
        self.assertEqual(res["sizing_used"], {"buy_pct": 0.5, "sell_tranches": 2})
        sells = [m for m in res["markers"] if m["side"] == "sell"]
        self.assertTrue(sells and all(m["leg"] is not None for m in sells if m["reason"] == "signal"))
        json.dumps(res, allow_nan=False)

    def test_rejects_bad_sizing(self):
        for bad in ({"buy_pct": 0}, {"buy_pct": 1.5}, {"buy_pct": float("nan")}, {"sell_tranches": 0},
                    {"sell_tranches": 11}, {"sell_tranches": 2.5}, {"sell_tranches": True}, {"nope": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                bt.merge_sizing(bad)


class TestValidation(unittest.TestCase):
    def test_rejects_unknown_strategy(self):
        with self.assertRaises(ValueError):
            bt.merge_params("nope", None)

    def test_rejects_inverted_windows(self):
        with self.assertRaises(ValueError):
            bt.merge_params("sma_crossover", {"short_window": 20, "long_window": 10})
        with self.assertRaises(ValueError):
            bt.merge_params("macd", {"fast": 30, "slow": 10})

    def test_rejects_unknown_or_bad_param_values(self):
        with self.assertRaises(ValueError):
            bt.merge_params("rsi", {"bogus": 1})
        with self.assertRaises(ValueError):
            bt.merge_params("rsi", {"window": "14"})
        with self.assertRaises(ValueError):
            bt.merge_params("rsi", {"buy_th": 150})
        with self.assertRaises(ValueError):
            bt.merge_params("rsi", {"window": float("nan")})
        with self.assertRaises(ValueError):
            bt.merge_params("rsi", {"window": True})

    def test_rejects_bad_risk(self):
        with self.assertRaises(ValueError):
            bt.merge_risk({"stop_loss_pct": 1.5})
        with self.assertRaises(ValueError):
            bt.merge_risk({"stop_loss_pct": 0})
        with self.assertRaises(ValueError):
            bt.merge_risk({"fee_bps": 5000})
        with self.assertRaises(ValueError):
            bt.merge_risk({"unknown": 1})

    def test_rejects_bad_capital_and_too_few_bars(self):
        with self.assertRaises(ValueError):
            bt.run_backtest(candles_from([1, 2, 3, 4]), "obv", initial_capital=0)
        with self.assertRaises(ValueError):
            bt.run_backtest(candles_from([1, 2]), "obv")  # 마지막 봉 제외하면 1개


if __name__ == "__main__":
    unittest.main()
