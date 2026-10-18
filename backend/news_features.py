"""
뉴스 피처 조인 + news_sentiment 신호 (순수 함수 — DB/네트워크 없음)
============================================================
[이 파일이 하는 일]
  봉 배열(시각)과 뉴스 신호 목록을 받아 봉마다 news_score / news_weight / news_count 를 만들고,
  이 값으로 news_sentiment 전략의 스탠스(+1 진입 / -1 청산 / 0 대기)를 만든다.
  설계: docs/news-signal-design.md 6.2~6.3절

[시간 누수 방지 규칙 — 가장 중요한 부분]
  봉 [t, t+Δ) 의 판단 시각은 '봉 마감 T = t+Δ' 이다.
  - T 이전에 수집된(collected_at < T) 기사만 쓴다. 정확히 T 에 수집된 기사는 다음 봉의 몫이다.
  - 기준 시각은 published_at 이 아니라 collected_at(시스템이 실제로 안 시각)이다.
  - 감쇠도 T 기준: w = r · exp(-(T - collected_at)/τ). collected_at < T 이므로 지수는 항상 0 이하.
    r 은 use_rel=1 이면 p_relevant, 기본(use_rel=0)은 1.
    (2026-10-17 대리 라벨 측정에서 relevance 정확도가 최빈 기준선보다 낮아(0.567 vs 0.587, AUC 0.62)
     관련도 필터·가중을 기본으로 끈다. 질문 문구를 고쳐 다시 재고 나아지면 use_rel=1 로 켠다.)
  - 발행(published_at)된 지 max_age_min 보다 오래된 기사는 제외한다 (첫 기동 때 RSS 가 주는 과거 기사 방지).
    published_at 이 없는 기사도 같은 이유로 제외한다.

[점수]
  s_i        = p_pos - p_neg                        (-1 ~ 1)
  news_weight = Σ w_i
  news_score  = Σ w_i·s_i / (Σ w_i + w0)           w0 사전 가중치: 기사가 적거나 오래되면 0 쪽으로 수축
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

MARKET_SYMBOL = "MARKET"

# signal-worker/questions.py 의 EVENT_TYPES 값과 같다 (질문 스키마가 바뀌면 함께 바꿀 것)
EVENT_TYPE_NAMES = [
    "listing_delisting", "regulation_legal", "hack_security", "institutional_etf_flow",
    "partnership_tech", "macro", "other",
]

# 파라미터: 이름 -> (기본값, 최소, 최대, 정수 여부)
NEWS_PARAM_SPECS: dict[str, tuple[float, float, float, bool]] = {
    "lookback_min": (240, 1, 10_080, True),     # 피처 계산 창(분)
    "tau_min": (120, 1, 10_080, True),          # 감쇠 시간 상수(분)
    "buy_th": (0.3, -1.0, 1.0, False),          # news_score 가 이 값 이상이면 진입
    "sell_th": (-0.2, -1.0, 1.0, False),        # 이 값 이하이면 청산
    "min_weight": (0.5, 0.0, 100.0, False),     # news_weight 가 이 값 미만이면 '신호 없음'. 0.5 = 기사 1건이 τ·ln2(약 83분) 안
    "max_age_min": (360, 1, 10_080, True),      # 발행 후 이 시간이 지난 기사는 제외
    "use_rel": (0, 0, 1, True),                 # 1 이면 p_relevant 로 거르고(rel_th) 가중한다. 기본 끔 (위 주석)
    "rel_th": (0.5, 0.0, 1.0, False),           # p_relevant 하한 (use_rel=1 일 때만)
    "w0": (0.5, 0.0, 100.0, False),             # 수축용 사전 가중치
    "confirm_combined": (0, 0, 1, True),        # 1 이면 복합 전략 스탠스가 약세가 아닐 때만 진입
    "market_w": (0.5, 0.0, 1.0, False),         # MARKET 신호 반영 비율
}
NEWS_DEFAULT_PARAMS: dict[str, float] = {k: v[0] for k, v in NEWS_PARAM_SPECS.items()}


def validate_news_params(overrides: dict[str, Any] | None) -> dict[str, float]:
    """기본값에 사용자 값을 덮어쓰고 범위를 검증한다 (ValueError)."""
    params = dict(NEWS_DEFAULT_PARAMS)
    for key, value in (overrides or {}).items():
        if key not in NEWS_PARAM_SPECS:
            raise ValueError(f"news_sentiment 에 없는 파라미터: {key}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"파라미터 {key} 는 유한한 숫자여야 합니다")
        _, lo, hi, is_int = NEWS_PARAM_SPECS[key]
        if not lo <= value <= hi:
            raise ValueError(f"파라미터 {key} 는 {lo:g}~{hi:g} 범위여야 합니다")
        params[key] = int(value) if is_int else float(value)
    if params["sell_th"] >= params["buy_th"]:
        raise ValueError("sell_th 는 buy_th 보다 작아야 합니다")
    return params


def _epoch(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    return float(pd.Timestamp(value).timestamp())


def news_features(
    times: list[int],
    interval_minutes: int,
    news: list[dict[str, Any]],
    params: dict[str, float],
    symbol: str | None = None,
) -> dict[str, Any]:
    """봉마다 뉴스 피처를 계산한다.

    times: 봉 시작 시각(epoch 초), 오름차순.
    news : [{symbol, collected_at, published_at, p_relevant, p_pos, p_neg, event_type?}, ...]
           symbol 을 주면 그 종목과 MARKET 신호만 쓴다.
    반환: {"score","weight","count": np.ndarray(n), "events": list[dict[str,int]]}  (events: 봉별 이벤트 유형 개수)
    """
    n = len(times)
    p = {**NEWS_DEFAULT_PARAMS, **params}
    lookback, tau = p["lookback_min"] * 60.0, p["tau_min"] * 60.0
    max_age = p["max_age_min"] * 60.0

    rows = []
    for item in news:
        sym = item.get("symbol")
        if symbol is not None and sym not in (symbol, MARKET_SYMBOL):
            continue
        rel, pos, neg = item.get("p_relevant"), item.get("p_pos"), item.get("p_neg")
        if pos is None or neg is None:
            continue
        if p["use_rel"]:
            if rel is None or rel < p["rel_th"]:
                continue
        else:
            rel = 1.0
        collected, published = _epoch(item.get("collected_at")), _epoch(item.get("published_at"))
        if collected is None or published is None or collected - published > max_age:
            continue
        mult = p["market_w"] if sym == MARKET_SYMBOL else 1.0
        rows.append((collected, float(rel) * mult, float(pos) - float(neg), item.get("event_type") or "other"))
    rows.sort(key=lambda r: r[0])

    c = np.array([r[0] for r in rows], dtype=float)
    rel_w = np.array([r[1] for r in rows], dtype=float)
    s = np.array([r[2] for r in rows], dtype=float)
    ev = [r[3] for r in rows]

    close = np.asarray(times, dtype=float) + interval_minutes * 60.0  # 봉 마감 T
    score = np.zeros(n)
    weight = np.zeros(n)
    count = np.zeros(n, dtype=int)
    events: list[dict[str, int]] = [{} for _ in range(n)]
    if len(rows):
        lo = np.searchsorted(c, close - lookback, side="left")  # collected_at >= T - lookback
        hi = np.searchsorted(c, close, side="left")             # collected_at <  T  (T 정각 수집분은 다음 봉)
        for k in range(n):
            if hi[k] <= lo[k]:
                continue
            sl = slice(lo[k], hi[k])
            w = rel_w[sl] * np.exp(-(close[k] - c[sl]) / tau)
            total = float(w.sum())
            weight[k] = total
            count[k] = hi[k] - lo[k]
            denom = total + p["w0"]
            score[k] = float((w * s[sl]).sum() / denom) if denom > 0 else 0.0
            counts: dict[str, int] = {}
            for e in ev[lo[k]:hi[k]]:
                counts[e] = counts.get(e, 0) + 1
            events[k] = counts
    return {"score": score, "weight": weight, "count": count, "events": events}


def news_stance(
    score: np.ndarray,
    weight: np.ndarray,
    params: dict[str, float],
    combined_stance: np.ndarray | None = None,
) -> tuple[np.ndarray, list[str | None]]:
    """news_sentiment 의 스탠스(+1 진입 / -1 청산 / 0 대기)와 청산 사유를 만든다.

    - 신호 유효: news_weight >= min_weight
    - 미보유(<=0): 유효 && score >= buy_th (&& confirm_combined 면 복합 스탠스 >= 0) -> +1
    - 보유(+1)   : 유효 && score <= sell_th -> -1 ('signal')
                   유효하지 않음(기사가 식거나 끊김) -> -1 ('news_expired')  -> 뉴스가 끊겨도 무한 보유하지 않는다
    반환: (stance, reasons)  reasons[i] 는 i 번 봉에서 -1 로 바뀔 때의 사유(그 외 None).
    """
    n = len(score)
    stance = np.zeros(n)
    reasons: list[str | None] = [None] * n
    state = 0.0
    confirm = bool(params.get("confirm_combined"))
    for i in range(n):
        valid = weight[i] >= params["min_weight"]
        if state <= 0:
            if valid and score[i] >= params["buy_th"]:
                if not confirm or combined_stance is None or combined_stance[i] >= 0:
                    state = 1.0
        else:
            if valid and score[i] <= params["sell_th"]:
                state, reasons[i] = -1.0, "signal"
            elif not valid:
                state, reasons[i] = -1.0, "news_expired"
        stance[i] = state
    return stance, reasons
