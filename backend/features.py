"""
봉별 피처 테이블 (기술 지표 + 뉴스 피처) — /features
============================================================
오프라인 ML 학습용 내보내기. 순수 함수라 DB 없이 테스트한다.

!! 이 표에는 정답(라벨)이 없다. 다음 봉 수익률 같은 라벨을 만들 때는 반드시 '이 봉 마감 이후' 값을 쓰고,
   시간 순서로 학습/검증을 나눌 것 (무작위 분할 금지: 시간 누수). !!

지표 정의는 backtest.py 전략과 같다 (같은 기본 파라미터):
  ret_1       직전 봉 대비 종가 수익률
  sma_3/15    단순 이동평균 (sma_crossover 기본값)
  macd, macd_signal   EMA(8)-EMA(17), 그 EMA(12)  (macd 기본값)
  rsi_14      단순 이동평균 RSI (midas 와 같은 방식, Wilder 아님)
  bb_mid/upper/lower  볼린저 밴드 (20, 2σ)
  obv, obv_sma_10     OBV 와 그 10봉 단순 평균
뉴스: news_score, news_weight, news_count, event_<유형> (news_features.py 참고)
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from news_features import EVENT_TYPE_NAMES


def technical_features(df: pd.DataFrame) -> pd.DataFrame:
    close, volume = df["Close"], df["Volume"]
    out = pd.DataFrame(index=df.index)
    out["ret_1"] = close.pct_change()
    out["sma_3"] = close.rolling(3).mean()
    out["sma_15"] = close.rolling(15).mean()
    macd = close.ewm(span=8, adjust=False).mean() - close.ewm(span=17, adjust=False).mean()
    out["macd"] = macd
    out["macd_signal"] = macd.ewm(span=12, adjust=False).mean()
    delta = close.diff()
    gain, loss = delta.where(delta > 0, 0), -delta.where(delta < 0, 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = gain.rolling(14).mean() / loss.rolling(14).mean()
    out["rsi_14"] = 100 - (100 / (1 + rs))
    mid, std = close.rolling(20).mean(), close.rolling(20).std()
    out["bb_mid"], out["bb_upper"], out["bb_lower"] = mid, mid + 2 * std, mid - 2 * std
    direction = np.where(close > close.shift(1), volume, np.where(close < close.shift(1), -volume, 0))
    obv = pd.Series(direction, index=df.index).cumsum()
    out["obv"] = obv
    out["obv_sma_10"] = obv.rolling(10).mean()
    return out


def build_feature_rows(
    candles: list[dict[str, Any]], news_feats: dict[str, Any],
) -> list[dict[str, Any]]:
    """봉 목록 + 뉴스 피처 -> JSON 으로 내보낼 행 목록 (NaN/inf 는 None)."""
    df = pd.DataFrame({
        "Close": [float(c["close"]) for c in candles],
        "Volume": [float(c["volume"]) for c in candles],
    })
    tech = technical_features(df)
    rows: list[dict[str, Any]] = []
    for i, c in enumerate(candles):
        row: dict[str, Any] = {
            "window_start": c["window_start"],
            "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"], "volume": c["volume"],
        }
        for col in tech.columns:
            row[col] = tech[col].iloc[i]
        row["news_score"] = news_feats["score"][i]
        row["news_weight"] = news_feats["weight"][i]
        row["news_count"] = int(news_feats["count"][i])
        counts = news_feats["events"][i]
        for name in EVENT_TYPE_NAMES:
            row[f"event_{name}"] = int(counts.get(name, 0))
        rows.append({k: _clean(v) for k, v in row.items()})
    return rows


def _clean(v: Any) -> Any:
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, np.integer):
        return int(v)
    return v
