"""일봉 백테스트 엔진 (벡터 연산, 롱 온리).

[체결 모델] 판단은 t 일 종가, 체결은 t+1 일 시가 (같은 봉 종가 체결의 낙관 편향을 없앤다).
  그래서 하루 수익률을 두 조각으로 나눈다.
    시가 갭(전일 종가 -> 시가) 은 '어제까지 들고 있던' 포지션이 받고,
    장중(시가 -> 종가) 은 '오늘 시가에 새로 잡은' 포지션이 받는다.
  수수료·슬리피지는 체결량(|포지션 변화|) x 편도 비용. 현금으로 있는 날은 단기 국채 금리(^IRX)를 받는다.
  가격은 배당·분할 조정가라 단순 보유도 배당 재투자 총수익이다.

midas-touch 의 _simulate (같은 봉 종가 체결, 정수 주식, 무조정가) 와 다르다. 이 차이 때문에 같은 전략이라도
수익률이 다르게 나올 수 있다.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import strategies as st

FEE_BPS = 5.0          # 편도 0.05% (미국 주식 수수료 0 + 스프레드·슬리피지 가정)
TRADING_DAYS = 252


def position_returns(df: pd.DataFrame, pos: pd.Series, rf_d: np.ndarray, fee: float):
    """포지션 시리즈 -> (일별 수익률, 장중 보유 배열)."""
    o = df["Open"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    p = np.nan_to_num(pos.to_numpy(float))
    n = len(c)
    held = np.zeros(n)
    held[1:] = p[:-1]                       # t-1 종가 결정 -> t 시가 체결 -> t 장중 보유
    held_gap = np.zeros(n)
    held_gap[1:] = held[:-1]                # t 시가 갭 구간엔 t-1 장중 보유분이 남아 있다
    gap = np.zeros(n)
    gap[1:] = np.divide(o[1:], c[:-1], out=np.ones(n - 1), where=c[:-1] > 0) - 1
    intra = np.divide(c, o, out=np.ones(n), where=o > 0) - 1
    turnover = np.abs(np.diff(held, prepend=0.0))
    r = (1 + held_gap * gap) * (1 + held * intra) - 1 - fee * turnover + (1 - held) * rf_d
    r[0] = 0.0
    return r, held


def breakout_returns(df: pd.DataFrame, level: pd.Series, rf_d: np.ndarray, fee: float):
    """변동성 돌파: 장중 고가가 기준가를 넘으면 max(기준가, 시가) 에 사서 다음 날 시가에 판다."""
    o, h, c = (df[k].to_numpy(float) for k in ("Open", "High", "Close"))
    lv = level.to_numpy(float)
    n = len(c)
    trig = np.nan_to_num(h >= lv, nan=0.0).astype(bool) & ~np.isnan(lv)
    trig[-1] = False                         # 다음 날 시가가 없으면 청산을 못 한다
    entry = np.maximum(lv, o)
    intra = np.where(trig, c / (entry * (1 + fee)) - 1, 0.0)
    overnight = np.zeros(n)
    overnight[1:] = np.where(trig[:-1], o[1:] * (1 - fee) / c[:-1] - 1, 0.0)
    held = trig.astype(float)
    r = (1 + overnight) * (1 + intra) - 1 + (1 - held) * rf_d
    r[0] = 0.0
    idx = np.where(trig)[0]
    trades = [{"entry": df.index[i], "exit": df.index[i + 1], "entry_px": float(entry[i]),
               "exit_px": float(o[i + 1]), "ret": float(o[i + 1] * (1 - fee) / (entry[i] * (1 + fee)) - 1),
               "days": 1, "open": False} for i in idx]
    return r, held, trades


def extract_trades(df: pd.DataFrame, held: np.ndarray, fee: float) -> list[dict]:
    """장중 보유 배열에서 거래 목록을 만든다. 진입·청산은 모두 시가 체결이다."""
    o, c = df["Open"].to_numpy(float), df["Close"].to_numpy(float)
    n = len(held)
    d = np.diff(np.r_[0.0, (held > 0).astype(float), 0.0])
    trades = []
    for e, x in zip(np.where(d > 0)[0], np.where(d < 0)[0]):
        still_open = x >= n
        exit_px = c[-1] if still_open else o[x]
        trades.append({
            "entry": df.index[e], "exit": df.index[-1] if still_open else df.index[x],
            "entry_px": float(o[e]), "exit_px": float(exit_px),
            "ret": float(exit_px * (1 - fee) / (o[e] * (1 + fee)) - 1),
            "days": int(x - e), "open": bool(still_open),
        })
    return trades


def run(strategy: st.Strategy, df: pd.DataFrame, params: dict, rf_d: np.ndarray, fee_bps: float = FEE_BPS):
    """전략 1개 x 파라미터 1개 -> {returns, held, trades, levels, series}."""
    fee = fee_bps / 10_000
    series, levels = strategy.fn(df, params)
    if strategy.kind == "breakout":
        r, held, trades = breakout_returns(df, series, rf_d, fee)
    else:
        r, held = position_returns(df, series, rf_d, fee)
        trades = extract_trades(df, held, fee)
    return {"returns": r, "held": held, "trades": trades, "levels": levels, "series": series}


def buy_hold(df: pd.DataFrame, rf_d: np.ndarray, fee_bps: float = FEE_BPS):
    pos = pd.Series(1.0, index=df.index)
    r, held = position_returns(df, pos, rf_d, fee_bps / 10_000)
    return {"returns": r, "held": held, "trades": extract_trades(df, held, fee_bps / 10_000)}


def _f(x, digits: int = 4):
    return None if x is None or not math.isfinite(x) else round(float(x), digits)


def sharpe(r: np.ndarray, rf_d: np.ndarray, ppy: int = TRADING_DAYS) -> float:
    """연환산 샤프 (초과수익 기준). ppy = 1년 봉 수 (주식 252, 코인 365). 표본이 적거나 변동이 없으면 0."""
    if len(r) < 20:
        return 0.0
    sd = r.std(ddof=1)
    return float((r - rf_d).mean() / sd * math.sqrt(ppy)) if sd > 0 else 0.0


def metrics(r: np.ndarray, rf_d: np.ndarray, held: np.ndarray, trades: list[dict], ppy: int = TRADING_DAYS) -> dict:
    eq = np.cumprod(1 + r)
    years = max(len(r) / ppy, 1 / ppy)
    ex = r - rf_d
    downside = math.sqrt(float(np.mean(np.minimum(ex, 0) ** 2))) if len(r) else 0.0
    dd = eq / np.maximum.accumulate(eq) - 1
    mdd = float(dd.min()) if len(dd) else 0.0
    cagr = float(eq[-1] ** (1 / years) - 1) if len(eq) and eq[-1] > 0 else -1.0
    closed = [t for t in trades if not t["open"]]
    wins = [t["ret"] for t in closed if t["ret"] > 0]
    losses = [t["ret"] for t in closed if t["ret"] <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    return {
        "total_return": _f(eq[-1] - 1 if len(eq) else 0.0),
        "cagr": _f(cagr),
        "vol": _f(float(r.std(ddof=1) * math.sqrt(ppy)) if len(r) > 1 else 0.0),
        "sharpe": _f(sharpe(r, rf_d, ppy), 3),
        "sortino": _f(float(ex.mean() / downside * math.sqrt(ppy)) if downside > 0 else 0.0, 3),
        "max_drawdown": _f(mdd),
        "calmar": _f(cagr / abs(mdd) if mdd < 0 else 0.0, 3),
        "trades": len(closed),
        "win_rate": _f(len(wins) / len(closed) if closed else 0.0, 3),
        "profit_factor": _f(min(gross_win / gross_loss, 99.0) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0), 3),
        "avg_win": _f(float(np.mean(wins)) if wins else 0.0),
        "avg_loss": _f(float(np.mean(losses)) if losses else 0.0),
        "avg_days": _f(float(np.mean([t["days"] for t in closed])) if closed else 0.0, 1),
        "exposure": _f(float(held.mean()) if len(held) else 0.0, 3),
    }


def equity_curve(r: np.ndarray, dates: pd.DatetimeIndex, points: int = 200) -> list[list]:
    """차트용 [epoch초, 누적값] (최대 points 개로 줄인다)."""
    eq = np.cumprod(1 + r)
    step = max(1, len(eq) // points)
    idx = list(range(0, len(eq), step))
    if idx[-1] != len(eq) - 1:
        idx.append(len(eq) - 1)
    return [[int(dates[i].timestamp()), round(float(eq[i]), 5)] for i in idx]
