"""전략 라이브러리 (일봉, 롱 온리).

각 전략 함수는 df(Open/High/Low/Close/Volume) 와 파라미터를 받아 (series, levels) 를 돌려준다.
  kind="position": series[t] = t 일 종가에 정한 목표 보유(1 보유 / 0 현금). 체결은 backtest.py 가 t+1 시가에 한다.
  kind="breakout": series[t] = t 일 장중 매수 기준가 (변동성 돌파). 체결 규칙은 backtest.breakout_returns.
  levels = 마지막 봉의 판단 근거 숫자 (화면의 '다음 신호 조건' 설명용).

family 는 '지금 미보유' 상태를 투표에서 어떻게 읽을지 정한다.
  trend     : 미보유 = 약세 신호 (추세가 꺾였다는 뜻)
  reversion : 미보유 = 중립 (진입 조건이 아직 안 왔을 뿐)
  day       : 하루짜리 매매라 투표에서 뺀다 (검증 결과와 내일 기준가만 보여 준다)

origin: midas-touch 에서 이식한 6개(로직은 kaspflow backend/backtest.py 와 같음, RSI 만 Wilder 로 통일)
        + 널리 알려진 규칙 5개.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

import indicators as ta


@dataclass(frozen=True)
class Strategy:
    key: str
    label: str
    family: str
    origin: str
    rule: str
    grid: tuple[dict, ...]
    fn: Callable[[pd.DataFrame, dict], tuple[pd.Series, dict]]
    kind: str = "position"


def _hold(entry: pd.Series, exit_: pd.Series) -> pd.Series:
    """진입/청산 이벤트 -> 보유 상태. 같은 날 둘 다면 청산이 이긴다 (midas 와 같음)."""
    state = pd.Series(np.nan, index=entry.index)
    state[entry.fillna(False).astype(bool)] = 1.0
    state[exit_.fillna(False).astype(bool)] = 0.0
    return state.ffill().fillna(0.0)


def _last(s: pd.Series) -> float | None:
    v = s.iloc[-1]
    return None if pd.isna(v) else float(v)


def _sma_cross(df, p):
    s, l = ta.sma(df.Close, p["short"]), ta.sma(df.Close, p["long"])
    return (s > l).astype(float), {f"SMA{p['short']}": _last(s), f"SMA{p['long']}": _last(l)}


def _macd(df, p):
    line, sig, _ = ta.macd(df.Close, p["fast"], p["slow"], p["signal"])
    return (line > sig).astype(float), {"MACD": _last(line), "시그널": _last(sig)}


def _rsi_cross(df, p):
    r = ta.rsi(df.Close, p["n"])
    entry = (r > p["buy"]) & (r.shift(1) <= p["buy"])
    exit_ = (r < p["sell"]) & (r.shift(1) >= p["sell"])
    return _hold(entry, exit_), {f"RSI{p['n']}": _last(r)}


def _bollinger(df, p):
    _, up, lo = ta.bollinger(df.Close, p["n"], p["k"])
    c = df.Close
    entry = (c > lo) & (c.shift(1) < lo.shift(1))
    exit_ = (c < up) & (c.shift(1) > up.shift(1))
    return _hold(entry, exit_), {"상단밴드": _last(up), "하단밴드": _last(lo)}


def _obv(df, p):
    o = ta.obv(df.Close, df.Volume)
    m = ta.sma(o, p["n"])
    return (o > m).astype(float), {"OBV": _last(o), f"OBV 평균{p['n']}": _last(m)}


def _combined(df, p):
    votes = sum(s.fn(df, s.grid[0])[0] for s in MIDAS_BASE)
    return (votes >= p["k"]).astype(float), {"보유 중인 하위 전략 수": _last(votes)}


def _donchian(df, p):
    hi = df.High.rolling(p["entry"], min_periods=p["entry"]).max().shift(1)
    lo = df.Low.rolling(p["exit"], min_periods=p["exit"]).min().shift(1)
    return _hold(df.Close > hi, df.Close < lo), {f"{p['entry']}일 최고가": _last(hi), f"{p['exit']}일 최저가": _last(lo)}


def _ts_momentum(df, p):
    ret = df.Close / df.Close.shift(p["n"]) - 1
    return (ret > 0).astype(float), {f"{p['n']}거래일 수익률": _last(ret)}


def _trend_filter(df, p):
    m = ta.sma(df.Close, p["n"])
    return (df.Close > m).astype(float), {f"SMA{p['n']}": _last(m)}


def _rsi2(df, p):
    r, m200, m5 = ta.rsi(df.Close, 2), ta.sma(df.Close, 200), ta.sma(df.Close, 5)
    entry = (r < p["th"]) & (df.Close > m200)
    return _hold(entry, df.Close > m5), {"RSI2": _last(r), "SMA200": _last(m200), "SMA5": _last(m5)}


def _vol_breakout(df, p):
    rng = (df.High - df.Low).shift(1)
    level = df.Open + p["k"] * rng
    # 내일 기준가는 내일 시가를 알아야 정해진다. 화면에는 '내일 시가 + k x 오늘 변동폭' 으로 보여 준다.
    return level, {"오늘 변동폭": _last(df.High - df.Low), "k": p["k"]}


MIDAS_BASE: tuple[Strategy, ...] = (
    Strategy("sma_cross", "이동평균 교차", "trend", "midas-touch",
             "단기 SMA 가 장기 SMA 위면 보유 (50/200 = 골든크로스)",
             ({"short": 3, "long": 15}, {"short": 10, "long": 50}, {"short": 20, "long": 100},
              {"short": 50, "long": 200}), _sma_cross),
    Strategy("macd", "MACD", "trend", "midas-touch",
             "MACD 선이 시그널 선 위면 보유",
             ({"fast": 8, "slow": 17, "signal": 12}, {"fast": 12, "slow": 26, "signal": 9},
              {"fast": 5, "slow": 35, "signal": 5}), _macd),
    Strategy("rsi_cross", "RSI 돌파", "reversion", "midas-touch",
             "RSI 가 매수선을 위로 뚫으면 진입, 매도선을 아래로 뚫으면 청산",
             ({"n": 14, "buy": 45, "sell": 65}, {"n": 14, "buy": 30, "sell": 70}), _rsi_cross),
    Strategy("bollinger", "볼린저 밴드 복귀", "reversion", "midas-touch",
             "종가가 하단 밴드 안으로 복귀하면 진입, 상단 밴드 안으로 복귀하면 청산",
             ({"n": 20, "k": 2.0}, {"n": 20, "k": 2.5}), _bollinger),
    Strategy("obv", "OBV 추세", "trend", "midas-touch",
             "OBV 가 자기 이동평균 위면 보유 (거래량이 상승을 받쳐 준다)",
             ({"n": 10}, {"n": 20}, {"n": 50}), _obv),
)

STRATEGIES: tuple[Strategy, ...] = MIDAS_BASE + (
    Strategy("combined", "복합 다수결", "trend", "midas-touch",
             "위 5개 전략(기본 파라미터) 중 k개 이상이 보유면 보유",
             ({"k": 3}, {"k": 4}), _combined),
    Strategy("donchian", "돈치안 돌파 (터틀)", "trend", "Richard Dennis 터틀 트레이딩",
             "N일 최고가 돌파 시 진입, M일 최저가 이탈 시 청산",
             ({"entry": 20, "exit": 10}, {"entry": 55, "exit": 20}), _donchian),
    Strategy("ts_momentum", "시계열 모멘텀", "trend", "Moskowitz-Ooi-Pedersen (2012)",
             "N거래일 수익률이 플러스면 보유 (절대 모멘텀)",
             ({"n": 63}, {"n": 126}, {"n": 252}), _ts_momentum),
    Strategy("trend_filter", "장기 추세 필터", "trend", "Faber (2007) 10개월 이동평균",
             "종가가 N일 이동평균 위면 보유",
             ({"n": 100}, {"n": 150}, {"n": 200}), _trend_filter),
    Strategy("rsi2", "RSI(2) 눌림목", "reversion", "Connors & Alvarez (2008)",
             "200일선 위에서 RSI(2) 가 기준 아래로 빠지면 진입, 종가가 5일선 위로 오면 청산",
             ({"th": 5}, {"th": 10}, {"th": 20}), _rsi2),
    Strategy("vol_breakout", "변동성 돌파", "day", "Larry Williams",
             "오늘 시가 + k x 전일 변동폭을 넘으면 매수, 다음 날 시가에 매도",
             ({"k": 0.4}, {"k": 0.5}, {"k": 0.6}), _vol_breakout, kind="breakout"),
)

BY_KEY = {s.key: s for s in STRATEGIES}


def n_variants() -> int:
    return sum(len(s.grid) for s in STRATEGIES)
