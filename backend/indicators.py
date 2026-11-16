"""기술적 지표 (pandas 만 사용).

모든 지표는 t 시점까지의 값만 써서 계산한다 (미래 참조 없음). 그래서 전체 시계열로 한 번
계산한 뒤 구간을 잘라 써도 그 구간 안에서 계산한 것과 같다 (워크포워드 검증이 이 성질을 쓴다).
평활은 Wilder 방식(alpha = 1/n)을 쓴다. midas-touch 의 백테스트 RSI 는 단순 이동평균이었는데,
스냅샷 쪽은 Wilder 라 둘이 어긋났다. 여기서는 Wilder 하나로 통일한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = _wilder(d.clip(lower=0), n)
    dn = _wilder(-d.clip(upper=0), n)
    out = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return out.mask((dn == 0) & up.notna(), 100.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, sig, line - sig


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return mid, mid + k * sd, mid - k * sd


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    return pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return _wilder(true_range(high, low, close), n)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    up, dn = high.diff(), -low.diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=high.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=high.index)
    tr = _wilder(true_range(high, low, close), n)
    pdi, mdi = 100 * _wilder(plus, n) / tr, 100 * _wilder(minus, n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return _wilder(dx, n)


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    return (np.sign(close.diff()).fillna(0) * volume).cumsum()
