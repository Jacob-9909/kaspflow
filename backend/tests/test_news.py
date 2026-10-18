"""
뉴스 피처 조인 · news_sentiment 전략 테스트 (DB/네트워크 불필요)
============================================================
핵심은 시간 누수 방지 규칙이다: 봉 마감 직전/정각/직후에 수집된 기사가 어느 봉에 들어가는지,
오래된 기사·발행 시각 없는 기사가 빠지는지, 신호가 식으면 청산되는지를 합성 데이터로 검증한다.

실행:  cd backend && python -m unittest discover -s tests -v
"""
import json
import math
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest as bt  # noqa: E402
import news_features as nf  # noqa: E402

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T0_EPOCH = int(T0.timestamp())
STEP = 15  # 분
# 기본: lookback 240, tau 120, rel_th 0.5, max_age 360, w0 0.5, market_w 0.5.
# 관련도 가중까지 검증하려고 use_rel=1 로 둔다 (운영 기본값 0 은 TestRelevanceOff 에서 따로 본다).
P = {**nf.NEWS_DEFAULT_PARAMS, "use_rel": 1}


def times(n=8):
    return [T0_EPOCH + STEP * 60 * i for i in range(n)]


def item(c_sec, pub_before_min=10, symbol="BTCUSDT", rel=0.9, pos=0.8, neg=0.1, event="other"):
    """collected_at = T0 + c_sec 초인 뉴스 신호 1건. published_at 은 그보다 pub_before_min 분 전(None 이면 없음)."""
    c = T0 + timedelta(seconds=c_sec)
    return {
        "symbol": symbol, "collected_at": c,
        "published_at": None if pub_before_min is None else c - timedelta(minutes=pub_before_min),
        "p_relevant": rel, "p_pos": pos, "p_neg": neg, "event_type": event,
    }


def feats(news, params=None, symbol=None, n=8):
    return nf.news_features(times(n), STEP, news, {**P, **(params or {})}, symbol=symbol)


class TestJoinBoundaries(unittest.TestCase):
    """봉 0 = [0s, 900s). 마감 T = 900s."""

    def test_article_just_before_close_belongs_to_this_bar(self):
        f = feats([item(899)])
        self.assertGreater(f["weight"][0], 0)
        self.assertEqual(f["count"][0], 1)

    def test_article_exactly_at_close_goes_to_next_bar(self):
        f = feats([item(900)])
        self.assertEqual(f["weight"][0], 0.0)
        self.assertEqual(f["count"][0], 0)
        self.assertGreater(f["weight"][1], 0)

    def test_article_just_after_close_goes_to_next_bar(self):
        f = feats([item(901)])
        self.assertEqual(f["count"][0], 0)
        self.assertEqual(f["count"][1], 1)

    def test_decay_is_measured_from_bar_close_and_never_exceeds_relevance(self):
        # 봉 안(600s)에서 수집: T=900 이므로 경과 300s. 가중치는 p_rel 이하여야 한다 (v0.1 수식은 1 을 넘었다)
        f = feats([item(600)])
        expected_w = 0.9 * math.exp(-300 / (P["tau_min"] * 60))
        self.assertAlmostEqual(f["weight"][0], expected_w, places=9)
        self.assertLessEqual(f["weight"][0], 0.9)
        self.assertAlmostEqual(f["score"][0], expected_w * 0.7 / (expected_w + P["w0"]), places=9)

    def test_lookback_window_edges(self):
        # lookback 30분, 기사는 0s. 봉 마감 900s/1800s 에는 들어가고(>= T-30분), 2700s 에는 빠진다
        f = feats([item(0)], {"lookback_min": 30})
        self.assertEqual([int(x) for x in f["count"][:3]], [1, 1, 0])

    def test_old_article_is_weaker_than_fresh_one(self):
        f = feats([item(0)], n=12)
        # 같은 기사 1건이라도 시간이 지나면 점수 크기가 줄어든다 (수축 평균)
        self.assertGreater(f["score"][0], f["score"][8])
        self.assertGreater(f["score"][8], 0)


class TestFilters(unittest.TestCase):
    def test_stale_or_unpublished_articles_are_excluded(self):
        f = feats([item(100, pub_before_min=400)])  # 발행 400분 전 > max_age 360
        self.assertEqual(f["count"][0], 0)
        f = feats([item(100, pub_before_min=None)])  # 발행 시각 없음
        self.assertEqual(f["count"][0], 0)
        f = feats([item(100, pub_before_min=300)])
        self.assertEqual(f["count"][0], 1)

    def test_max_age_boundary_is_inclusive(self):
        self.assertEqual(feats([item(100, pub_before_min=360)])["count"][0], 1)
        self.assertEqual(feats([item(100, pub_before_min=361)])["count"][0], 0)

    def test_relevance_threshold(self):
        self.assertEqual(feats([item(100, rel=0.49)])["count"][0], 0)
        self.assertEqual(feats([item(100, rel=0.5)])["count"][0], 1)

    def test_relevance_off_by_default_keeps_all_and_weights_equally(self):
        off = {"use_rel": 0}
        self.assertEqual(nf.NEWS_DEFAULT_PARAMS["use_rel"], 0)
        f = feats([item(600, rel=0.1), item(600, rel=None)], off)
        self.assertEqual(f["count"][0], 2)
        self.assertAlmostEqual(f["weight"][0], 2 * math.exp(-300 / (P["tau_min"] * 60)), places=9)

    def test_single_fresh_article_passes_default_min_weight(self):
        # 감쇠 때문에 기사 1건의 가중치는 항상 1 미만 -> min_weight 기본값이 1 이상이면 1건으로는 신호가 안 난다
        f = feats([item(600)], {"use_rel": 0})
        self.assertGreaterEqual(f["weight"][0], nf.NEWS_DEFAULT_PARAMS["min_weight"])

    def test_market_signal_is_scaled_and_other_symbols_ignored(self):
        f = feats([item(600, symbol="MARKET"), item(600, symbol="ETHUSDT")], symbol="BTCUSDT")
        decay = math.exp(-300 / (P["tau_min"] * 60))
        self.assertEqual(f["count"][0], 1)  # ETH 는 빠지고 MARKET 만
        self.assertAlmostEqual(f["weight"][0], 0.9 * 0.5 * decay, places=9)

    def test_event_counts(self):
        f = feats([item(100, event="macro"), item(200, event="macro"), item(300, event="hack_security")])
        self.assertEqual(f["events"][0], {"macro": 2, "hack_security": 1})

    def test_no_news_is_all_zero(self):
        f = feats([])
        self.assertTrue((f["score"] == 0).all() and (f["weight"] == 0).all() and (f["count"] == 0).all())


class TestStance(unittest.TestCase):
    PARAMS = {**P, "min_weight": 1.0, "buy_th": 0.3, "sell_th": -0.2}

    def stance(self, score, weight, combined=None, **over):
        return nf.news_stance(np.array(score, float), np.array(weight, float), {**self.PARAMS, **over},
                              None if combined is None else np.array(combined, float))

    def test_buy_then_sell_on_threshold(self):
        st, why = self.stance([0, 0.5, 0.1, -0.3, 0.0], [2, 2, 2, 2, 2])
        self.assertEqual(list(st), [0, 1, 1, -1, -1])
        self.assertEqual(why[3], "signal")

    def test_weak_weight_blocks_entry(self):
        st, _ = self.stance([0.9, 0.9], [0.5, 0.99])
        self.assertEqual(list(st), [0, 0])

    def test_expiry_exits_instead_of_holding_forever(self):
        st, why = self.stance([0.5, 0.4, 0.4, 0.4], [2, 2, 0.2, 0.0])
        self.assertEqual(list(st), [1, 1, -1, -1])
        self.assertEqual(why[2], "news_expired")

    def test_reentry_after_exit(self):
        st, _ = self.stance([0.5, -0.5, 0.5], [2, 2, 2])
        self.assertEqual(list(st), [1, -1, 1])

    def test_confirm_combined_blocks_bearish_entry(self):
        st, _ = self.stance([0.5, 0.5], [2, 2], combined=[-1, 0], confirm_combined=1)
        self.assertEqual(list(st), [0, 1])


def candles(n=40, up=False):
    rows = []
    for i in range(n):
        close = 100.0 + (i if up else 0)
        rows.append({"window_start": T0 + timedelta(minutes=STEP * i), "open": close + 1, "high": close + 2,
                     "low": close - 2, "close": close, "volume": 1000.0})
    return rows


def bar_sec(k, offset=100):
    """봉 k 의 시작 + offset 초."""
    return STEP * 60 * k + offset


def positive_at(k, n=2):
    return [item(bar_sec(k, 100 + 10 * j)) for j in range(n)]


class TestNewsStrategyBacktest(unittest.TestCase):
    RISK = {"fee_bps": 0}

    def run_news(self, news, fill="next_open", params=None, **kw):
        return bt.run_backtest(candles(), "news_sentiment", params=params, risk=self.RISK,
                               interval_minutes=STEP, fill=fill, news=news, **kw)

    def test_signal_bar_close_then_fill_next_open(self):
        news = positive_at(5)  # 봉 5 안에서 수집 -> 봉 5 마감에 진입 판단
        res = self.run_news(news, fill="next_open")
        buy = next(m for m in res["markers"] if m["side"] == "buy")
        self.assertEqual(buy["time"], T0_EPOCH + STEP * 60 * 6)   # 체결은 봉 6
        self.assertEqual(buy["price"], 101.0)                      # 봉 6 의 시가
        res_close = self.run_news(news, fill="close")
        buy_c = next(m for m in res_close["markers"] if m["side"] == "buy")
        self.assertEqual(buy_c["time"], T0_EPOCH + STEP * 60 * 5)  # close 체결은 신호 봉 5
        self.assertEqual(buy_c["price"], 100.0)

    def test_article_after_bar_close_cannot_trigger_that_bar(self):
        # 봉 5 의 마감 정각(=봉 6 시작)에 수집된 기사 -> 봉 5 에서는 못 쓰고 봉 6 마감에 판단 -> 봉 7 시가 체결
        news = [item(STEP * 60 * 6, 5), item(STEP * 60 * 6 + 10, 5)]
        res = self.run_news(news, fill="next_open")
        buy = next(m for m in res["markers"] if m["side"] == "buy")
        self.assertEqual(buy["time"], T0_EPOCH + STEP * 60 * 7)

    def test_negative_news_sells(self):
        neg = [item(bar_sec(8, 10 * j), pos=0.05, neg=0.9) for j in range(4)]  # 신호가 식기 전에 악재
        res = self.run_news(positive_at(5) + neg)
        self.assertEqual([m["side"] for m in res["markers"]], ["buy", "sell"])
        self.assertEqual(res["trades"][0]["exit_reason"], "signal")

    def test_cooled_news_exits_with_news_expired(self):
        res = self.run_news(positive_at(5))  # 이후 새 기사가 없어 신호가 식는다
        self.assertEqual(res["trades"][0]["exit_reason"], "news_expired")
        self.assertIsNone(res["open_position"])

    def test_no_news_means_no_trades_and_warns(self):
        for news in (None, []):
            res = self.run_news(news)
            self.assertEqual(res["trades"], [])
            self.assertTrue(any("뉴스 신호가 없습니다" in w for w in res["warnings"]))
            json.dumps(res, allow_nan=False)

    def test_few_trades_warn_to_withhold_judgement(self):
        res = self.run_news(positive_at(5))
        self.assertTrue(any("판단을 보류" in w for w in res["warnings"]))

    def test_stale_only_news_is_reported(self):
        res = self.run_news([item(bar_sec(5), pub_before_min=500)] * 3)
        self.assertEqual(res["trades"], [])
        self.assertTrue(any("조건" in w for w in res["warnings"]))

    def test_confirm_combined_veto(self):
        bearish = lambda df, strategy, params: pd.Series(-1.0, index=df.index)  # noqa: E731
        with mock.patch.object(bt, "compute_position", side_effect=bearish):
            res = self.run_news(positive_at(5), params={"confirm_combined": 1})
        self.assertEqual(res["trades"], [])
        res = self.run_news(positive_at(5), params={"confirm_combined": 0})
        self.assertTrue(res["trades"])

    def test_result_is_strict_json_and_reports_news_summary(self):
        res = self.run_news(positive_at(5))
        json.dumps(res, allow_nan=False)
        self.assertEqual(res["news"]["articles"], 2)
        self.assertGreater(res["news"]["bars_with_signal"], 0)
        self.assertEqual(res["strategy"], "news_sentiment")


class TestBaselines(unittest.TestCase):
    def test_compare_adds_combined_and_buy_hold(self):
        res = bt.run_backtest(candles(up=True), "news_sentiment", risk={"fee_bps": 0}, interval_minutes=STEP,
                              news=positive_at(5), compare=True)
        base = res["baselines"]
        self.assertEqual(set(base), {"combined", "buy_hold"})
        for b in base.values():
            self.assertEqual(set(b), {"total_return", "max_drawdown", "trade_count", "win_rate", "sharpe_ratio"})
        # 단순 보유: 봉 1 종가에서 사서 (마지막 미확정 봉을 뺀) 마지막 종가까지 든다
        rows = candles(up=True)[:-1]
        self.assertAlmostEqual(base["buy_hold"]["total_return"], rows[-1]["close"] / rows[1]["close"] - 1, places=6)
        json.dumps(res, allow_nan=False)

    def test_baselines_absent_by_default(self):
        res = bt.run_backtest(candles(), "macd", risk={"fee_bps": 0}, interval_minutes=STEP)
        self.assertNotIn("baselines", res)

    def test_baselines_use_same_fill(self):
        kw = dict(risk={"fee_bps": 0}, interval_minutes=STEP, news=positive_at(5), compare=True)
        a = bt.run_backtest(candles(up=True), "news_sentiment", fill="close", **kw)["baselines"]["buy_hold"]
        b = bt.run_backtest(candles(up=True), "news_sentiment", fill="next_open", **kw)["baselines"]["buy_hold"]
        self.assertNotEqual(a["total_return"], b["total_return"])


class TestNewsParamValidation(unittest.TestCase):
    def test_defaults_and_floats(self):
        p = bt.merge_params("news_sentiment", {"buy_th": 0.45, "sell_th": -0.35, "lookback_min": 120})
        self.assertEqual((p["buy_th"], p["sell_th"], p["lookback_min"]), (0.45, -0.35, 120))
        self.assertEqual(bt.merge_params("news_sentiment", None), nf.NEWS_DEFAULT_PARAMS)

    def test_rejects_bad_values(self):
        for bad in ({"bogus": 1}, {"buy_th": 2}, {"sell_th": -1.5}, {"tau_min": 0}, {"buy_th": "0.3"},
                    {"buy_th": True}, {"min_weight": float("nan")}, {"buy_th": 0.1, "sell_th": 0.1},
                    {"confirm_combined": 2}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                bt.merge_params("news_sentiment", bad)

    def test_listed_with_other_strategies(self):
        self.assertIn("news_sentiment", bt.STRATEGY_LABELS)
        self.assertEqual(bt.DEFAULT_PARAMS["news_sentiment"], nf.NEWS_DEFAULT_PARAMS)

    def test_cannot_compute_position_without_news(self):
        with self.assertRaises(ValueError):
            bt.compute_position(pd.DataFrame({"Close": [1.0, 2.0]}), "news_sentiment", {})


if __name__ == "__main__":
    unittest.main()
