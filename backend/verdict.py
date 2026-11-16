"""판단 엔진: 모듈 점수를 기간별 가중치로 합쳐 매수 / 관망 / 매도를 정한다.

각 모듈은 score(-1~+1) 와 confidence(0~1) 를 낸다.
  S = Σ(w x c x s) / Σ w   (쓸 수 있는 모듈만)
신뢰도가 낮은 모듈은 점수를 0 쪽으로 끌어당긴다. 근거가 약하면 '관망'으로 기운다는 뜻이다.
  S >= +0.15 매수, S <= -0.15 매도, 그 사이 관망. |S| >= 0.35 면 '강한'.
판단 자체는 이 계산이 정한다 (재현 가능). LLM 위원장은 이유를 풀어 쓰기만 하고 판단을 바꾸지 못한다.

기간별 가중치 근거
  단기: 가격 추세·검증된 전략·뉴스·시장 국면이 주도. 밸류에이션은 단기 수익을 거의 설명하지 못한다.
  장기: 밸류에이션·재무 품질·투자 대가 관점이 주도. 단기 기술 신호의 비중은 작다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel

import indicators as ta

MODULES = {
    "technical": "기술적 지표", "strategies": "검증된 전략 신호", "analogs": "과거 유사 국면", "sentiment": "뉴스 감성",
    "consensus": "애널리스트 컨센서스", "valuation": "밸류에이션", "quality": "재무 품질", "personas": "투자 대가 패널",
    "regime": "시장 국면",
}
WEIGHTS = {
    "short": {"technical": .22, "strategies": .22, "analogs": .08, "sentiment": .16, "consensus": .06,
              "valuation": .04, "quality": .02, "personas": .05, "regime": .15},
    "mid": {"technical": .12, "strategies": .15, "analogs": .06, "sentiment": .10, "consensus": .12,
            "valuation": .15, "quality": .12, "personas": .10, "regime": .08},
    "long": {"technical": .04, "strategies": .05, "analogs": .02, "sentiment": .04, "consensus": .10,
             "valuation": .28, "quality": .25, "personas": .18, "regime": .04},
}
BUY_TH, STRONG_TH = 0.15, 0.35
LABEL = {"buy": "매수", "hold": "관망", "sell": "매도"}


def clip(x, lo=-1.0, hi=1.0):
    return float(max(lo, min(hi, x)))


def grade(score: float) -> str:
    return "A" if score >= 0.4 else "B" if score >= 0.15 else "C" if score > -0.15 else "D" if score > -0.4 else "F"


def _ret(c: pd.Series, n: int) -> float | None:
    return float(c.iloc[-1] / c.iloc[-1 - n] - 1) if len(c) > n else None


def technical(df: pd.DataFrame, spy: pd.Series) -> dict:
    c, h, l = df["Close"], df["High"], df["Low"]
    if len(c) < 60:
        return {"available": False, "score": 0.0, "confidence": 0.0, "summary": "가격 기록이 짧습니다.", "detail": {}, "levels": {}}
    s20, s50, s200 = ta.sma(c, 20), ta.sma(c, 50), ta.sma(c, 200)
    r14 = ta.rsi(c, 14)
    _, _, hist = ta.macd(c)
    _, bb_up, bb_lo = ta.bollinger(c)
    adx = ta.adx(h, l, c)
    ob = ta.obv(c, df["Volume"])
    last = float(c.iloc[-1])
    have200 = not pd.isna(s200.iloc[-1])
    above50, above200 = last > s50.iloc[-1], (last > s200.iloc[-1]) if have200 else None
    parts: list[tuple[float, float]] = []
    reasons = []
    if have200:
        t = 1.0 if above200 and above50 else 0.25 if above200 else -0.25 if above50 else -1.0
        golden = bool(s50.iloc[-1] > s200.iloc[-1])
        t = clip(t + (0.3 if golden else -0.3))
        a = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 20.0
        t *= 1.2 if a > 25 else 0.7 if a < 15 else 1.0
        parts.append((0.35, clip(t)))
        reasons.append(f"{'200일선 위' if above200 else '200일선 아래'}, {'골든크로스' if golden else '데드크로스'} 상태, ADX {a:.0f}")
    r6, r12_1 = _ret(c, 126), (float(c.iloc[-22] / c.iloc[-253] - 1) if len(c) > 253 else None)
    moms = [x for x in (r6, r12_1) if x is not None]
    if moms:
        parts.append((0.25, clip(float(np.mean(moms)) / 0.3)))
    rel = None
    if r6 is not None and len(spy) > 127:
        spy6 = float(spy.iloc[-1] / spy.iloc[-127] - 1)
        rel = r6 - spy6
        parts.append((0.15, clip(rel / 0.2)))
    rv = float(r14.iloc[-1])
    parts.append((0.10, -0.5 if rv > 70 else 0.5 if rv < 30 else 0.25 if rv >= 50 else -0.25))
    hv, hp = float(hist.iloc[-1]), float(hist.iloc[-4]) if len(hist) > 4 else 0.0
    parts.append((0.10, (0.5 if hv > hp else 0.2) if hv > 0 else (-0.2 if hv > hp else -0.5)))
    if len(c) > 21:
        p20, o20 = c.iloc[-1] - c.iloc[-21], ob.iloc[-1] - ob.iloc[-21]
        parts.append((0.05, 0.25 if np.sign(p20) == np.sign(o20) and p20 > 0 else -0.25 if np.sign(p20) != np.sign(o20) else -0.1))
    score = sum(w * s for w, s in parts) / sum(w for w, _ in parts)
    reasons.append(f"RSI14 {rv:.0f}, MACD 히스토그램 {'+' if hv > 0 else '-'}{'(확대)' if abs(hv) > abs(hp) else '(축소)'}")
    if moms:
        reasons.append(f"6개월 수익률 {r6:+.1%}" + (f", SPY 대비 {rel:+.1%}" if rel is not None else ""))
    tail = c.tail(252)
    levels = {k: (None if pd.isna(v) else round(float(v), 4)) for k, v in {
        "SMA20": s20.iloc[-1], "SMA50": s50.iloc[-1], "SMA200": s200.iloc[-1], "볼린저 상단": bb_up.iloc[-1],
        "볼린저 하단": bb_lo.iloc[-1], "20일 최고": h.tail(20).max(), "20일 최저": l.tail(20).min(),
        "52주 최고": tail.max(), "52주 최저": tail.min()}.items()}
    return {"available": True, "score": round(score, 3), "confidence": 0.9 if have200 else 0.6,
            "summary": " / ".join(reasons), "levels": levels,
            "detail": {"above_50": bool(above50), "above_200": above200, "rsi14": round(rv, 1), "adx": None if pd.isna(adx.iloc[-1]) else round(float(adx.iloc[-1]), 1),
                       "ret_1m": _ret(c, 21), "ret_3m": _ret(c, 63), "ret_6m": r6, "ret_12m": _ret(c, 252),
                       "rel_strength_6m": rel, "macd_hist": round(hv, 4)}}


def regime(spy: pd.Series, vix: pd.Series, tnx: pd.Series, sector: pd.Series | None, sector_sym: str | None,
           beta: float | None) -> dict:
    if len(spy) < 210:
        return {"available": False, "score": 0.0, "confidence": 0.0, "summary": "시장 데이터가 부족합니다.", "detail": {}}
    s200 = ta.sma(spy, 200)
    above = bool(spy.iloc[-1] > s200.iloc[-1])
    spy3 = float(spy.iloc[-1] / spy.iloc[-64] - 1)
    s = (0.5 if above else -0.5) + (0.25 if spy3 > 0 else -0.25)
    v = float(vix.iloc[-1]) if len(vix) else None
    if v is not None:
        s += 0.25 if v < 18 else 0.0 if v < 25 else -0.25 if v < 32 else -0.5
    dt = float(tnx.iloc[-1] - tnx.iloc[-64]) if len(tnx) > 64 else None
    if dt is not None:
        s += -0.25 if dt > 0.5 else 0.15 if dt < -0.5 else 0.0
    sec_above = None
    if sector is not None and len(sector) > 210:
        sec_above = bool(sector.iloc[-1] > ta.sma(sector, 200).iloc[-1])
        s += 0.25 if sec_above else -0.25
    b = 1.0 if beta is None else max(0.6, min(1.4, beta))
    score = clip(clip(s) * b)
    bits = [f"SPY {'200일선 위' if above else '200일선 아래'} (3개월 {spy3:+.1%})"]
    if v is not None:
        bits.append(f"VIX {v:.1f}")
    if dt is not None:
        bits.append(f"10년물 3개월 변화 {dt:+.2f}%p")
    if sec_above is not None:
        bits.append(f"섹터 {sector_sym} {'200일선 위' if sec_above else '200일선 아래'}")
    return {"available": True, "score": round(score, 3), "confidence": 0.8, "summary": ", ".join(bits),
            "detail": {"spy_above_200": above, "spy_ret_3m": round(spy3, 4), "vix": v, "tnx_change_3m": dt,
                       "sector_etf": sector_sym, "sector_above_200": sec_above, "beta_used": round(b, 2)}}


def combine(modules: dict[str, dict], horizon: str, risk: dict, earnings_days: int | None) -> dict:
    w = WEIGHTS[horizon]
    used = {k: m for k, m in modules.items() if k in w and m.get("available") and m.get("confidence", 0) > 0}
    wsum = sum(w[k] for k in used) or 1.0
    contrib = {k: w[k] * m["confidence"] * m["score"] / wsum for k, m in used.items()}
    score = sum(contrib.values())
    wc = sum(w[k] * m["confidence"] for k, m in used.items())
    absum = sum(w[k] * m["confidence"] * abs(m["score"]) for k, m in used.items())
    agreement = abs(sum(w[k] * m["confidence"] * m["score"] for k, m in used.items())) / absum if absum > 0 else 0.0
    coverage = wc / wsum
    conf = coverage * (0.5 + 0.5 * agreement)
    notes = []
    if earnings_days is not None and 0 <= earnings_days <= 10:
        conf *= 0.85
        notes.append(f"{earnings_days}일 뒤 실적 발표: 발표 전후 변동이 커 신뢰도를 낮췄습니다.")
    if (risk.get("vol_1y") or 0) > 0.6:
        conf *= 0.9
        notes.append("연 변동성이 60%를 넘는 고위험 종목입니다.")
    missing = [MODULES[k] for k in w if k not in used]
    if missing:
        notes.append("판단에서 빠진 모듈: " + ", ".join(missing))
    v = "buy" if score >= BUY_TH else "sell" if score <= -BUY_TH else "hold"
    return {"horizon": horizon, "verdict": v, "label": LABEL[v], "strong": abs(score) >= STRONG_TH and v != "hold",
            "score": round(score, 4), "confidence": round(conf, 3), "agreement": round(agreement, 3),
            "coverage": round(coverage, 3),
            "contributions": {k: round(x, 4) for k, x in sorted(contrib.items(), key=lambda kv: -abs(kv[1]))},
            "weights": {k: w[k] for k in w}, "notes": notes}


def advice(v: dict, position: dict | None, price: float, plan: dict) -> dict:
    holding = bool(position and (position.get("qty") or 0) > 0)
    pnl = (price / position["avg_price"] - 1) if holding and position.get("avg_price") else None
    stop, target = plan["stop"], plan["target"]
    if v["verdict"] == "buy":
        text = (f"보유 유지. 추가 매수는 계좌 대비 {plan['max_weight']:.0%} 비중 안에서." if holding
                else f"매수 고려. 진입 ${plan['entry']:,.2f}, 손절 ${stop:,.2f} ({plan['stop_pct']:+.1%}), "
                     f"목표 ${target:,.2f} ({plan['target_pct']:+.1%}). 1회 손실을 계좌의 1%로 묶으면 비중 최대 {plan['max_weight']:.0%}.")
    elif v["verdict"] == "sell":
        if holding:
            text = ("비중 축소·매도 고려. " + (f"평가이익 {pnl:+.1%} 구간이라 일부 이익 실현이 선택지입니다." if pnl and pnl > 0
                                          else f"평가손익 {pnl:+.1%}. 손절 기준을 다시 확인하세요." if pnl is not None else ""))
        else:
            text = "신규 매수 비추천. 보유 중이라면 매도 쪽 신호입니다."
    else:
        text = (f"보유 유지. 종가가 ${stop:,.2f} 아래로 내려가면 정리 기준으로 삼으세요." if holding
                else "관망. 아래 '판단이 바뀌는 조건'이 나올 때까지 기다리세요.")
    return {"holding": holding, "pnl": None if pnl is None else round(pnl, 4), "text": text}


class Narrative(BaseModel):
    headline: str
    summary: str
    bull: list[str]
    bear: list[str]
    risks: list[str]
    triggers: list[str]


CIO_SYSTEM = ("너는 투자위원회 위원장이다. 계량 엔진이 이미 정한 판단을 사람에게 설명한다. 판단을 바꾸지 않고, "
              "주어진 숫자와 문장만 근거로 쓴다. 담백한 한국어로, 과장·확신·투자 권유 표현 없이 쓴다.")


def cio_prompt(symbol: str, name: str, price: float, verdicts: dict, selected: str, modules: dict, levels: dict,
               plan: dict, personas: dict, news_drivers: list, risk: dict) -> str:
    lines = [f"종목 {symbol} ({name}), 현재가 ${price:,.2f}",
             "기간별 판단: " + ", ".join(f"{k}={v['label']}(점수 {v['score']:+.2f}, 신뢰도 {v['confidence']:.0%})" for k, v in verdicts.items()),
             f"설명할 기간: {selected}", "", "[모듈별 결과]"]
    for k, m in modules.items():
        if m.get("available"):
            lines.append(f"- {MODULES.get(k, k)}: 점수 {m['score']:+.2f}, 신뢰도 {m['confidence']:.0%}. {m.get('summary', '')}")
    lines += ["", "[주요 가격대] " + ", ".join(f"{k} ${v:,.2f}" for k, v in levels.items() if v),
              f"[매매 계획] 손절 ${plan['stop']:,.2f}, 목표 ${plan['target']:,.2f}, 목표 먼저 도달 {plan['p_target_first']:.0%}, 손절 먼저 {plan['p_stop_first']:.0%}",
              f"[리스크] 연 변동성 {risk['vol_1y']:.0%}, 1년 최대 낙폭 {risk['mdd_1y']:.0%}, 베타 {risk.get('beta')}"]
    for p in (personas.get("members") or {}).values():
        if p.get("view"):
            lines.append(f"[{p['name']}] {p['view']['signal']} {p['view']['confidence']}: {p['view']['thesis']}")
    for d in news_drivers[:5]:
        lines.append(f"[뉴스 {d['date']}] ({d['sentiment']:+.1f}) {d['title']}")
    lines += ["", "작성할 것:",
              "- headline: 판단을 요약한 한 줄 (40자 이내)",
              "- summary: 3~4문장. 왜 이 판단인지, 가장 큰 근거 두세 개를 숫자와 함께",
              "- bull: 매수 쪽 근거 3개 이내, bear: 매도 쪽 근거 3개 이내",
              "- risks: 판단을 틀리게 만들 수 있는 위험 3개 이내",
              "- triggers: 판단이 바뀌는 조건 3개 이내. 가능하면 위 가격대 숫자를 써서 (예: 종가가 SMA200 $X 아래로 내려가면)"]
    return "\n".join(lines)


def fallback_narrative(v: dict, modules: dict, plan: dict) -> dict:
    ranked = sorted(((k, m) for k, m in modules.items() if m.get("available")), key=lambda kv: kv[1]["score"] * kv[1]["confidence"])
    bear = [f"{MODULES[k]}: {m['summary']}" for k, m in ranked if m["score"] < -0.1][:3]
    bull = [f"{MODULES[k]}: {m['summary']}" for k, m in reversed(ranked) if m["score"] > 0.1][:3]
    return {"headline": f"{v['label']} (점수 {v['score']:+.2f})",
            "summary": "AI 해설을 만들지 못해 계량 결과만 보여 줍니다. 모듈별 점수와 근거를 확인하세요.",
            "bull": bull, "bear": bear, "risks": v.get("notes", [])[:3],
            "triggers": [f"종가가 손절가 ${plan['stop']:,.2f} 아래로 내려가면 재평가", f"종가가 목표가 ${plan['target']:,.2f} 를 넘으면 재평가"]}
