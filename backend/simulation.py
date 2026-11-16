"""몬테카를로 시뮬레이션, 매매 계획, 유사 국면, 리스크 지표.

forward_paths: 최근 3년 일간 로그수익률을 정상 블록 부트스트랩(Politis & Romano 1994, 평균 블록 10일)으로
  다시 뽑아 앞으로 1년 가격 경로 2,000개를 만든다. 두꺼운 꼬리와 변동성 군집이 어느 정도 남는다.
  과거 평균 수익(드리프트)은 빼고 CAPM 기대수익(무위험 금리 + 베타 x 5%)으로 바꾼다. 과거에 많이 오른
  종목일수록 시뮬레이션도 낙관적으로 나오는 편향을 막기 위해서다. 그래서 이 결과는 방향 예측이 아니라
  '얼마나 흔들릴 수 있나(가격 범위, 손절·목표 도달 확률)' 를 보는 도구다. 방향 점수는 내지 않는다.
trade_plan: ATR 배수로 손절·목표가를 잡고, 위 경로에서 어느 쪽에 먼저 닿는지 센다 (종가 기준).
analogs: 지금과 같은 국면(200일선 위/아래, 3개월 모멘텀 부호, RSI 구간)이었던 과거 날들의 N일 뒤 수익률을
  전체 평균과 비교한다. 겹치는 구간 때문에 표본이 실제보다 많아 보이므로 유효 표본 수를 n / (2H/3) 로 줄인다.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import indicators as ta

HORIZONS = {"short": 21, "mid": 63, "long": 252}
HORIZON_LABEL = {"short": "단기 (1개월)", "mid": "중기 (3개월)", "long": "장기 (1년)"}
ATR_MULT = {"short": (2.0, 4.0), "mid": (3.0, 6.0), "long": (5.0, 10.0)}
ERP = 0.045           # 주식 위험 프리미엄 (fundamentals.ERP 와 같은 값)
RISK_PER_TRADE = 0.01  # 계좌 대비 1회 손실 한도
MAX_WEIGHT = 0.25


def _q(a: np.ndarray, p: float) -> float:
    return round(float(np.percentile(a, p)), 4)


def bootstrap_idx(length: int, n_paths: int, horizon: int, block: float, rng) -> np.ndarray:
    idx = np.empty((n_paths, horizon), dtype=np.int64)
    idx[:, 0] = rng.integers(0, length, n_paths)
    new = rng.random((n_paths, horizon)) < 1.0 / block
    starts = rng.integers(0, length, (n_paths, horizon))
    for t in range(1, horizon):
        idx[:, t] = np.where(new[:, t], starts[:, t], (idx[:, t - 1] + 1) % length)
    return idx


def bars(h: int, ppy: int) -> int:
    """주식 거래일 기준 기간을 이 자산의 봉 수로 (코인은 365일 거래)."""
    return max(1, round(h * ppy / 252))


def forward_paths(close: np.ndarray, drift_annual: float, ppy: int = 252, n_paths: int = 2000,
                  block: float = 10.0, seed: int = 11) -> np.ndarray:
    """앞으로 1년(ppy 봉) 가격 경로. 최근 3년 수익률을 재표본한다."""
    rng = np.random.default_rng(seed)
    horizon, lookback = ppy, 3 * ppy
    lr = np.diff(np.log(close[-(lookback + 1):]))
    mu = math.log(1 + drift_annual) / ppy - lr.var(ddof=1) / 2
    centered = lr - lr.mean() + mu
    idx = bootstrap_idx(len(centered), n_paths, horizon, block, rng)
    return close[-1] * np.exp(np.cumsum(centered[idx], axis=1))


def summarize_paths(paths: np.ndarray, p0: float, ppy: int = 252) -> dict:
    out = {}
    for name, h in HORIZONS.items():
        h = bars(h, ppy)
        final = paths[:, h - 1] / p0 - 1
        worst = np.sort(final)[: max(1, len(final) // 20)]
        out[name] = {"days": h, "p_up": round(float((final > 0).mean()), 3), "median": _q(final, 50),
                     "p5": _q(final, 5), "p25": _q(final, 25), "p75": _q(final, 75), "p95": _q(final, 95),
                     "cvar95": round(float(-worst.mean()), 4)}
    step = 4
    days = list(range(step - 1, paths.shape[1], step))
    band = np.percentile(paths[:, days], [5, 25, 50, 75, 95], axis=0)
    out["fan"] = {"days": [d + 1 for d in days], "p5": band[0].round(4).tolist(), "p25": band[1].round(4).tolist(),
                  "p50": band[2].round(4).tolist(), "p75": band[3].round(4).tolist(), "p95": band[4].round(4).tolist()}
    return out


def trade_plan(price: float, atr_value: float, paths: np.ndarray, horizon: str,
               kelly_stats: dict | None = None, ppy: int = 252) -> dict:
    m_stop, m_tgt = ATR_MULT[horizon]
    h = bars(HORIZONS[horizon], ppy)
    stop = max(price - m_stop * atr_value, price * 0.5)
    target = price + m_tgt * atr_value
    sub = paths[:, :h]
    never = h + 1
    hit_t, hit_s = sub >= target, sub <= stop
    first_t = np.where(hit_t.any(axis=1), hit_t.argmax(axis=1), never)
    first_s = np.where(hit_s.any(axis=1), hit_s.argmax(axis=1), never)
    p_t = float(np.mean(first_t < first_s))
    p_s = float(np.mean(first_s < first_t))
    none = (first_t == never) & (first_s == never)
    rest = float((sub[none, -1] / price - 1).mean()) if none.any() else 0.0
    ev = p_t * (target / price - 1) + p_s * (stop / price - 1) + float(none.mean()) * rest
    stop_dist = (price - stop) / price
    plan = {
        "entry": round(price, 4), "stop": round(stop, 4), "target": round(target, 4),
        "stop_pct": round(-stop_dist, 4), "target_pct": round(target / price - 1, 4),
        "reward_risk": round((target - price) / (price - stop), 2) if price > stop else None,
        "p_target_first": round(p_t, 3), "p_stop_first": round(p_s, 3), "p_neither": round(float(none.mean()), 3),
        "expected_return": round(ev, 4), "atr": round(atr_value, 4), "atr_mult": [m_stop, m_tgt],
        "max_weight": round(min(RISK_PER_TRADE / stop_dist, MAX_WEIGHT), 3) if stop_dist > 0 else None,
        "kelly_half": None,
    }
    if kelly_stats and kelly_stats.get("avg_loss"):
        w, r = kelly_stats["win_rate"], kelly_stats["avg_win"] / abs(kelly_stats["avg_loss"])
        if r > 0:
            plan["kelly_half"] = round(min(max((w - (1 - w) / r) / 2, 0.0), MAX_WEIGHT), 3)
    return plan


def _rsi_zone(r: float) -> int:
    return 0 if r < 30 else (2 if r > 70 else 1)


ZONE_LABEL = {0: "RSI 과매도(<30)", 1: "RSI 중립(30~70)", 2: "RSI 과매수(>70)"}


def analogs(df: pd.DataFrame, horizon: str, ppy: int = 252) -> dict:
    h = bars(HORIZONS[horizon], ppy)
    c = df["Close"]
    s200, mom, r = ta.sma(c, 200), c / c.shift(bars(63, ppy)) - 1, ta.rsi(c, 14)
    ok = s200.notna() & mom.notna() & r.notna()
    state = ((c > s200).astype(int) * 100 + (mom > 0).astype(int) * 10
             + r.fillna(50).map(_rsi_zone)).where(ok)
    fwd = c.shift(-h) / c - 1
    valid = ok & fwd.notna()
    if valid.sum() < 250 or pd.isna(state.iloc[-1]):
        return {"available": False, "score": 0.0, "confidence": 0.0, "summary": "유사 국면을 비교할 기록이 부족합니다."}
    cur = int(state.iloc[-1])
    desc = " · ".join([("200일선 위" if cur // 100 else "200일선 아래"),
                       ("3개월 모멘텀 +" if (cur // 10) % 10 else "3개월 모멘텀 -"), ZONE_LABEL[cur % 10]])
    same = valid & (state == cur)
    n = int(same.sum())
    all_f = fwd[valid]
    if n < 20:
        return {"available": False, "score": 0.0, "confidence": 0.0, "state": desc,
                "summary": f"지금 국면({desc})이 과거에 {n}일뿐이라 비교하지 않습니다."}
    f = fwd[same]
    n_eff = n / max(1.0, 2 * h / 3)
    se = float(f.std(ddof=1)) / math.sqrt(max(n_eff, 1.0))
    t = (float(f.mean()) - float(all_f.mean())) / se if se > 0 else 0.0
    hist, edges = np.histogram(f.clip(-0.6, 0.6), bins=24, range=(-0.6, 0.6))
    return {
        "available": True, "state": desc, "horizon_days": h, "n": n, "n_eff": round(n_eff, 1),
        "mean": round(float(f.mean()), 4), "median": round(float(f.median()), 4),
        "hit_rate": round(float((f > 0).mean()), 3),
        "base_mean": round(float(all_f.mean()), 4), "base_hit_rate": round(float((all_f > 0).mean()), 3),
        "t_stat": round(t, 2), "score": round(float(np.clip(t / 3, -1, 1)), 3),
        "confidence": round(min(1.0, n_eff / 10), 3),
        "hist": {"counts": hist.tolist(), "edges": edges.round(3).tolist()},
        "summary": (f"과거 같은 국면({desc}) {n}일의 {h}거래일 뒤 평균 수익 {f.mean():+.1%} "
                    f"(상승 확률 {(f > 0).mean():.0%}), 전체 평균은 {all_f.mean():+.1%}."),
    }


def beta(stock: pd.Series, market: pd.Series, days: int = 504) -> float | None:
    j = pd.concat([stock.pct_change(), market.pct_change()], axis=1, join="inner").dropna().tail(days)
    if len(j) < 120 or j.iloc[:, 1].var() == 0:
        return None
    return float(j.iloc[:, 0].cov(j.iloc[:, 1]) / j.iloc[:, 1].var())


def risk_profile(df: pd.DataFrame, spy_close: pd.Series, ppy: int = 252) -> dict:
    c = df["Close"]
    r = c.pct_change().dropna()
    vol = float(r.tail(ppy).std(ddof=1) * math.sqrt(ppy))

    def mdd(s: pd.Series) -> float:
        return float((s / s.cummax() - 1).min())

    a = ta.atr(df["High"], df["Low"], c, 14).iloc[-1]
    b = beta(c, spy_close)
    level = "낮음" if vol < 0.20 else "보통" if vol < 0.35 else "높음" if vol < 0.60 else "매우 높음"
    return {"vol_1y": round(vol, 4), "beta": None if b is None else round(b, 3),
            "mdd_1y": round(mdd(c.tail(ppy)), 4), "mdd_5y": round(mdd(c.tail(5 * ppy)), 4),
            "var95_1d": round(float(-np.percentile(r.tail(2 * ppy), 5)), 4),
            "atr": round(float(a), 4), "atr_pct": round(float(a / c.iloc[-1]), 4), "level": level}
