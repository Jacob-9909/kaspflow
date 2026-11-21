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

import re

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
HORIZON_DAYS = {"short": 30, "mid": 91, "long": 365}   # 적중 평가까지 기다리는 달력 일수
LABEL = {"buy": "매수", "hold": "관망", "sell": "매도"}


def judge(verdict: str, ret: float) -> bool:
    """midas-touch 판단 원장과 같은 기준: 매수는 +2% 초과, 매도는 -2% 미만, 관망은 ±5% 이내면 적중."""
    return ret > 0.02 if verdict == "buy" else ret < -0.02 if verdict == "sell" else abs(ret) <= 0.05


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
        strength = "추세 뚜렷" if a > 25 else "추세 약함" if a < 15 else "추세 보통"
        reasons.append(f"{'200일선 위' if above200 else '200일선 아래'}, {'골든크로스' if golden else '데드크로스'}, {strength}(ADX {a:.0f})")
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
    # 요약은 사람이 읽는 말로: 기호(/, +, 확대) 대신 뜻을 쓴다. 이 문장은 화면과 AI 해설 양쪽에 들어간다
    if moms:
        reasons.append(f"6개월 {r6:+.1%}" + (f"(S&P500보다 {rel * 100:+.1f}%p)" if rel is not None else ""))
    rsi_word = "과열" if rv > 70 else "과매도" if rv < 30 else "보통"
    reasons.append(f"RSI {rv:.0f}({rsi_word}), MACD {'상승' if hv > 0 else '하락'} 힘 {'커지는 중' if abs(hv) > abs(hp) else '줄어드는 중'}")
    tail = c.tail(252)
    levels = {k: (None if pd.isna(v) else round(float(v), 4)) for k, v in {
        "20일선": s20.iloc[-1], "50일선": s50.iloc[-1], "200일선": s200.iloc[-1], "볼린저 상단": bb_up.iloc[-1],
        "볼린저 하단": bb_lo.iloc[-1], "20일 최고": h.tail(20).max(), "20일 최저": l.tail(20).min(),
        "52주 최고": tail.max(), "52주 최저": tail.min()}.items()}
    return {"available": True, "score": round(score, 3), "confidence": 0.9 if have200 else 0.6,
            "summary": ", ".join(reasons), "levels": levels,
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
    bits = [f"S&P500 {'200일선 위' if above else '200일선 아래'}(3개월 {spy3:+.1%})"]
    if v is not None:
        bits.append(f"VIX {v:.1f}({'안정' if v < 18 else '보통' if v < 25 else '높음' if v < 32 else '매우 높음'})")
    if dt is not None:
        bits.append(f"10년 금리 3개월간 {dt:+.2f}%p")
    if sec_above is not None:
        bits.append(f"업종 {sector_sym} {'200일선 위' if sec_above else '200일선 아래'}")
    return {"available": True, "score": round(score, 3), "confidence": 0.8, "summary": ", ".join(bits),
            "detail": {"spy_above_200": above, "spy_ret_3m": round(spy3, 4), "vix": v, "tnx_change_3m": dt,
                       "sector_etf": sector_sym, "sector_above_200": sec_above, "beta_used": round(b, 2)}}


def combine(modules: dict[str, dict], horizon: str, risk: dict, earnings_days: int | None, weights: dict | None = None) -> dict:
    w = (weights or WEIGHTS)[horizon]
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


ORDER = ("short", "mid", "long")
HORIZON_KO = {"short": "단기 1개월", "mid": "중기 3개월", "long": "장기 1년"}


def _nearest(price: float, levels: dict | None) -> tuple[tuple[float, str] | None, tuple[float, str] | None]:
    """현재가 바로 위와 바로 아래 가격대 (값, 이름). 없으면 None.
    현재가와 사실상 같은 가격대는 뺀다 (52주 최저가에 닿은 날 '52주 최저 $125.53 위로 올라서면' 이라고 말하지 않게)."""
    lv = levels or {}
    above = sorted((v, k) for k, v in lv.items() if v and v > price * 1.0001)
    below = sorted(((v, k) for k, v in lv.items() if v and v < price * 0.9999), reverse=True)
    return (above[0] if above else None), (below[0] if below else None)


def _opposite(v: dict, verdicts: dict | None) -> tuple[str | None, str | None]:
    """설명할 기간보다 짧은 기간, 긴 기간 가운데 판단이 반대(매수 대 매도)인 기간. 없으면 None."""
    opp = {"buy": "sell", "sell": "buy"}.get(v["verdict"])
    if not verdicts or not opp or v.get("horizon") not in ORDER:
        return None, None
    i = ORDER.index(v["horizon"])
    hit = lambda hs: next((h for h in hs if (verdicts.get(h) or {}).get("verdict") == opp), None)
    return hit(ORDER[:i]), hit(ORDER[i + 1:])


def advice(v: dict, position: dict | None, price: float, plan: dict, verdicts: dict | None = None, levels: dict | None = None) -> dict:
    """판단을 '그래서 지금 무엇을 할지' 로 옮긴다. action 은 한마디, text 는 이유와 기준.
    다른 기간의 판단이 반대면(장기 매수인데 단기 매도 등) 그 사정에 맞춰 말한다. 규칙으로만 만들고 AI 는 쓰지 않는다."""
    holding = bool(position and (position.get("qty") or 0) > 0)
    pnl = (price / position["avg_price"] - 1) if holding and position.get("avg_price") else None
    stop, target = plan["stop"], plan["target"]
    shorter, longer = _opposite(v, verdicts)
    own = HORIZON_KO.get(v.get("horizon"), "이 기간")
    above, below = _nearest(price, levels)
    if v["verdict"] == "buy":
        if holding:
            action, text = "계속 보유", f"계속 보유하세요. 더 산다면 계좌의 {plan['max_weight']:.0%} 안에서 사세요."
        else:
            if shorter:
                action, lead = "나눠서 매수", f"길게 보면 살 만하지만 {HORIZON_KO[shorter]} 판단은 매도입니다. 더 내릴 수 있으니 한 번에 사지 말고 나눠 사세요."
            elif longer:
                action, lead = "짧게 매수", f"{own} 기준으로는 살 만하지만 {HORIZON_KO[longer]} 판단은 매도입니다. 오래 들고 가기보다 목표가와 손절가를 지키세요."
            else:
                action, lead = "매수 고려", "매수를 고려할 만합니다."
            text = (f"{lead} 진입 ${plan['entry']:,.2f}, 손절 ${stop:,.2f}({plan['stop_pct']:+.1%}), 목표 ${target:,.2f}({plan['target_pct']:+.1%}). "
                    f"한 번에 잃는 돈을 계좌의 1%로 제한하면 최대 비중은 {plan['max_weight']:.0%}입니다.")
    elif v["verdict"] == "sell":
        if holding:
            action = "비중 줄이기"
            text = ("비중을 줄이거나 파는 것을 고려하세요. " + (f"평가이익이 {pnl:+.1%}라 일부 이익을 실현하는 방법도 있습니다." if pnl and pnl > 0
                                                     else f"평가손익은 {pnl:+.1%}입니다. 손절 기준을 다시 확인하세요." if pnl is not None else ""))
        elif longer:
            # 하락이 멈췄는지 볼 기준선: 200일선이 현재가 위에 있으면 그 선, 아니면 바로 위 가격대
            ma200 = (levels or {}).get("200일선")
            ref = (ma200, "200일선") if ma200 and ma200 > price else above
            wait = f"종가가 {ref[1]} ${ref[0]:,.2f} 위로 올라서는지" if ref else "하락이 멈추는지"
            action, text = "사지 않기", f"지금은 사지 마세요. {HORIZON_KO[longer]} 판단은 매수이니, 길게 보고 살 생각이면 {wait} 본 뒤 다시 판단하세요."
        elif shorter:
            action, text = "사지 않기", f"새로 사지 않는 편이 낫습니다. {HORIZON_KO[shorter]} 판단은 매수라 잠깐 오를 수 있지만 {own} 기준으로는 매도 쪽입니다."
        else:
            action, text = "사지 않기", "새로 사지 않는 편이 낫습니다. 보유 중이라면 매도 쪽 신호입니다."
    elif holding:
        action, text = "계속 보유", f"계속 보유하되, 종가가 ${stop:,.2f} 아래로 내려가면 정리를 고려하세요."
    else:
        parts = ([f"{above[1]} ${above[0]:,.2f} 위로 올라서면 매수 쪽으로"] if above else []) + \
                ([f"{below[1]} ${below[0]:,.2f} 아래로 내려가면 매도 쪽으로"] if below else [])
        action = "지켜보기"
        text = "지금은 사지 말고 지켜보세요. " + (f"종가가 {', '.join(parts)} 기웁니다." if parts else "아래 '판단이 바뀌는 조건'이 나오면 다시 판단합니다.")
    return {"holding": holding, "pnl": None if pnl is None else round(pnl, 4), "action": action, "text": text}


class Narrative(BaseModel):
    headline: str
    summary: str
    bull: list[str]
    bear: list[str]
    risks: list[str]
    triggers: list[str]


CIO_SYSTEM = """너는 증권사에서 오래 일한 선배다. 계량 엔진이 이미 정한 판단을, 주식을 잘 모르는 후배가 옆에서 물었다고 생각하고 풀어 준다.
판단을 바꾸지 않고, 주어진 숫자와 문장만 근거로 쓴다. 과장, 확신, 투자 권유 표현은 쓰지 않는다.

[쉬운 말]
- 업계 용어는 뜻을 풀어 쓴다: '밸류에이션 매력' 은 '회사 가치에 비해 주가가 싸다', '모멘텀' 은 '오르는 힘', '상대 강도 우위' 는 'S&P500보다 더 올랐다',
  '변동성 확대' 는 '주가가 크게 출렁일 수 있다', '컨센서스' 는 '애널리스트 평균 의견', '펀더멘털' 은 '회사 실적과 재무'.
- 지표 숫자에는 그 숫자가 뜻하는 상태를 붙인다. 예) 'RSI 15' 만 쓰지 않고 'RSI 15로 지나치게 많이 팔린 상태'.
- 근거를 나열하지 말고 가장 큰 것 하나를 먼저 말한다. 한 문장에 숫자는 둘까지만.

[문체]
- 담백한 한국어. 문장 끝맺음은 항목마다 다르다.
  headline: 명사형 한 줄. 예) '실적은 탄탄하지만 주가가 적정가의 2배', '회사는 싸고 탄탄한데 주가는 아직 내려가는 중'
  summary: 모든 문장을 '-습니다', '-입니다' 로 끝낸다. 예) '영업이익률이 32%로 업종 평균보다 높습니다.'
  bull, bear, risks, triggers 항목: '-음', '-함' 이나 명사로 끝나는 짧은 구. '-습니다' 로 끝내지 않는다.
    예) '영업이익률 32%로 업종 상위', '부채가 적어 금리 부담이 작음', '종가가 50일선 $95 아래로 내려가면 매도 쪽으로 바뀜'
- 한 문장에 한 가지 뜻만 담고 60자 안팎으로 끊는다.
- 번역투를 쓰지 않는다: '~에 대해', '~에 있어', '~를 통해', '~로 인한', '~에 의해', '가지고 있다', '상회/하회' 대신 '넘다/밑돌다'.
- 상투구를 쓰지 않는다: '신중한 접근이 필요', '주목할 필요', '~할 것으로 보입니다', '양호한 모습', '상충', '시사'.
- 기호와 영어 단어를 풀어 쓴다: 'vs' 는 '~와 비교하면', '/' 는 쉼표, 'buy' 는 '매수'. RSI, MACD, PER, PBR, ADX, VIX, S&P500 같은 지표 이름은 그대로 쓴다.

[숫자]
- 숫자는 주어진 값과 표기를 그대로 옮긴다. 금액은 '$164.34' 처럼 쓰고 '달러' 로 바꾸지 않는다. 퍼센트의 기준과 방향을 바꾸지 않는다.
  예: '현재가가 적정가의 2.1배' 를 '현재가가 적정가보다 53% 높다' 나 '96% 높다' 로 바꾸면 틀린다. 배수는 배수로, 상승 여력은 상승 여력으로 쓴다.
- 배수는 '현재가가 적정가의 2.1배' 형태로만 쓴다. '~보다 2.1배 높다' 는 뜻이 모호해 쓰지 않는다.
- 점수는 -100(매도 쪽)~+100(매수 쪽) 척도다."""


def cio_prompt(symbol: str, name: str, price: float, verdicts: dict, horizon: str, modules: dict, levels: dict,
               plan: dict, personas: dict, news_drivers: list, risk: dict, position: dict | None = None) -> str:
    holding = bool(position and (position.get("qty") or 0) > 0)
    sel = verdicts[horizon]
    lines = [f"종목 {symbol} ({name}), 현재가 ${price:,.2f}",
             f"설명할 판단: {HORIZON_KO[horizon]} '{sel['label']}' (종합 점수 {sel['score'] * 100:+.0f}, 신뢰도 {sel['confidence']:.0%})",
             "다른 기간 판단(참고만, 설명에 섞지 않는다): " + ", ".join(
                 f"{HORIZON_KO[k]} '{v['label']}'({v['score'] * 100:+.0f})" for k, v in verdicts.items() if k != horizon),
             "보유 여부: " + (f"보유 중 (평균 단가 ${position['avg_price']:,.2f})" if holding and position.get("avg_price") else "보유 중" if holding else "보유하지 않음"),
             "", "[모듈별 결과]"]
    for k, m in modules.items():
        if m.get("available"):
            lines.append(f"- {MODULES.get(k, k)}: 점수 {m['score'] * 100:+.0f}, 신뢰도 {m['confidence']:.0%}. {m.get('summary', '')}")
    val = modules.get("valuation") or {}
    if val.get("available") and val.get("fair_value"):
        fair = val["fair_value"]
        lines.append(f"- 적정가 비교: 추정 적정가 ${fair:,.2f}, " + (f"상승 여력 {fair / price - 1:+.0%}" if fair >= price
                     else f"현재가가 적정가의 {price / fair:.1f}배"))
    above = sorted((v, k) for k, v in levels.items() if v and v > price)
    below = sorted(((v, k) for k, v in levels.items() if v and v <= price), reverse=True)
    near = lambda xs: ", ".join(f"{k} ${v:,.2f}({v / price - 1:+.1%})" for v, k in xs) or "없음"
    lines += ["", f"[주요 가격대] 현재가 ${price:,.2f} 기준. 위: {near(above)}. 아래: {near(below)}",
              f"[매매 계획] 손절가 ${plan['stop']:,.2f}({plan['stop_pct']:+.1%}), 목표가 ${plan['target']:,.2f}({plan['target_pct']:+.1%}). "
              f"기간 안에 목표가에 먼저 닿을 확률 {plan['p_target_first']:.0%}, "
              f"손절가에 먼저 닿을 확률 {plan['p_stop_first']:.0%}",
              f"[리스크] 연 변동성 {risk['vol_1y']:.0%}, 1년 최대 낙폭 {risk['mdd_1y']:.0%}, 베타 {'-' if risk.get('beta') is None else f"{risk['beta']:.2f}"}"]
    for p in (personas.get("members") or {}).values():
        if p.get("view"):
            lines.append(f"[{p['name']}] {p['view']['signal']} {p['view']['confidence']}: {p['view']['thesis']}")
    for d in news_drivers[:5]:
        lines.append(f"[뉴스 {d['date']}] (감성 {d['sentiment'] * 100:+.0f}) {d['title']}")
    lines += ["", "작성할 것:",
              "- headline: 판단의 핵심 이유를 담은 한 줄, 30자 안팎. '매수', '관망', '매도' 같은 판단 이름과 기간은 화면에 따로 있으니 넣지 않는다.",
              "- summary: 3문장. 첫째 판단을 이끈 가장 큰 근거, 둘째 반대쪽의 가장 큰 근거, 셋째 앞으로 지켜볼 점.",
              "  설명할 판단은 위의 하나뿐이다. '매도 판단이 섭니다' 처럼 다른 판단을 쓰지 않는다.",
              "  판단은 화면에 크게 나오므로 '중기 판단은 관망입니다' 같은 문장은 쓰지 않는다. '-85점' 같은 모듈 점수 대신 그 점수를 만든 사실(적정가, 수익률 등)을 쓴다.",
              "- bull: 매수 쪽 근거 3개 이내, bear: 매도 쪽 근거 3개 이내. 한 항목에 근거 하나, 40자 안팎.",
              "- risks: 판단을 틀리게 만들 수 있는 위험 3개 이내.",
              "- triggers: 판단이 바뀌는 조건 3개 이내. 위 가격대 숫자를 쓴다(예: 종가가 200일선 $X 아래로 내려가면 매도 쪽으로).",
              "  현재가보다 위에 있는 가격대는 '위로 올라서면', 아래에 있는 가격대는 '아래로 내려가면' 으로만 쓴다.",
              "  보유하지 않았다면 '매수를 검토할 조건' 과 '판단이 매도로 바뀌는 조건' 을 쓰고, 손절이나 익절을 '실행' 하라고 쓰지 않는다.",
              "  보유 중이라면 '정리를 고려할 조건' 과 '더 살 수 있는 조건' 을 쓴다."]
    return "\n".join(lines)


_VERDICT_TALK = re.compile(r"(매수|관망|매도)\s*(판단|의견|결론|신호가 우세)")


def tidy_summary(text: str, label: str) -> str:
    """요약에서 설명할 판단과 다른 판단을 말하는 문장을 뺀다. LLM 이 다른 기간의 판단을 섞어 쓰는 일이 있다
    (예: 단기 관망인데 장기 판단을 따라 '매도 판단이 섭니다'). 다 빠지면 원문을 둔다."""
    own = label.replace("강한 ", "")
    sents = [x for x in re.split(r"(?<=[.!?])\s+", text.strip()) if x]
    keep = [x for x in sents if all(m.group(1) == own for m in _VERDICT_TALK.finditer(x))]
    return " ".join(keep) if keep else text


def noun_end(s: str) -> str:
    """제목과 목록 항목을 '-음/-함' 끝맺음으로 맞춘다. AI 가 실행마다 '-습니다' 와 '-음' 을 섞어 쓴다.
    '높습니다' -> '높음', 받침 ㅂ + '니다' -> 받침 ㅁ ('보입니다' -> '보임', '큽니다' -> '큼', '상태입니다' -> '상태임')."""
    s = s.strip().rstrip(".")
    if s.endswith("습니다"):
        return s[:-3] + "음"
    if len(s) > 2 and s.endswith("니다"):
        ch = ord(s[-3]) - 0xAC00
        if 0 <= ch < 11172 and ch % 28 == 17:  # 한글 음절의 받침 번호: ㅂ 17, ㅁ 16
            return s[:-3] + chr(0xAC00 + ch - 1)
    return s


_PRICE = re.compile(r"\$([\d,]+(?:\.\d+)?)")
_UP = re.compile(r"위로|넘|돌파|올라")
_DOWN = re.compile(r"아래로|밑으로|내려|하회|이탈|깨지")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def check_triggers(items: list[str], price: float) -> list[str]:
    """'판단이 바뀌는 조건' 에서 현재가와 방향이 맞지 않는 항목을 뺀다 (예: 이미 200일선 아래인데
    '200일선 아래로 내려가면'). 쉼표로 나눈 절마다 가격과 방향어를 짝지어 본다. 가격이 없는 조건은 둔다."""
    ok = []
    for t in items:
        bad = False
        for part in t.split(","):
            m = _PRICE.search(part)
            if not m:
                continue
            x = float(m.group(1).replace(",", ""))
            up, down = bool(_UP.search(part)), bool(_DOWN.search(part))
            bad |= (up and not down and x <= price) or (down and not up and x >= price)
        if not bad:
            ok.append(t)
    return ok


def price_triggers(price: float, levels: dict, verdict: str) -> list[str]:
    """현재가 바로 위와 아래 가격대로 만든 조건. AI 조건이 검사에서 빠져 모자랄 때 채운다."""
    above, below = _nearest(price, levels)
    up_to = {"buy": "매수 근거가 강해짐", "hold": "매수 쪽으로 기움", "sell": "관망 쪽으로 기움"}[verdict]
    down_to = {"buy": "관망 쪽으로 기움", "hold": "매도 쪽으로 기움", "sell": "매도 근거가 강해짐"}[verdict]
    out = []
    if above:
        out.append(f"종가가 {above[1]} ${above[0]:,.2f} 위로 올라서면 {up_to}")
    if below:
        out.append(f"종가가 {below[1]} ${below[0]:,.2f} 아래로 내려가면 {down_to}")
    return out


def grounded(text: str, facts: set[float]) -> bool:
    """문장 속 숫자가 모두 AI 에 준 입력에 있는가. 반올림한 값도 인정한다(2.1배 -> '2배', $164.34 -> '$164')."""
    for t in _NUM.findall(text):
        s = t.replace(",", "")
        d = len(s.split(".")[1]) if "." in s else 0
        if not any(round(f, d) == float(s) for f in facts):
            return False
    return True


def tidy_narrative(n: dict, v: dict, prompt: str, price: float, levels: dict) -> tuple[dict, int]:
    """AI 해설을 화면에 내기 전에 바로잡는다. 실행마다 달리 나오는 실수를 코드로 막는다:
    다른 판단을 섞은 문장, 입력에 없는 숫자, 현재가와 방향이 맞지 않는 조건, 들쭉날쭉한 끝맺음.
    돌려주는 두 번째 값은 뺀 문장·항목 수 (결과 meta 에 남긴다)."""
    facts = {float(t.replace(",", "")) for t in _NUM.findall(prompt)}
    dropped = 0

    def keep(items: list[str]) -> list[str]:
        nonlocal dropped
        ok = [x for x in items if grounded(x, facts)]
        dropped += len(items) - len(ok)
        return ok

    out = {k: [noun_end(x) for x in keep(val)] if isinstance(val, list) else val for k, val in n.items()}
    trig = check_triggers(out.get("triggers", []), price)
    dropped += len(out.get("triggers", [])) - len(trig)
    if len(trig) < 2:
        trig += [t for t in price_triggers(price, levels, v["verdict"]) if not any(_PRICE.search(t).group(0) in x for x in trig)]
    out["triggers"] = trig[:3]
    sents = [x for x in re.split(r"(?<=[.!?])\s+", tidy_summary(n.get("summary", ""), v["label"]).strip()) if x]
    ok = keep(sents)
    out["summary"] = " ".join(ok) if ok else n.get("summary", "")
    head = noun_end(n.get("headline", ""))
    if not grounded(head, facts):
        dropped += 1
        first = (out.get("bull") if v["verdict"] == "buy" else out.get("bear")) or out.get("bull") or out.get("bear") or [""]
        head = first[0] or head
    out["headline"] = head
    return out, dropped


def fallback_narrative(v: dict, modules: dict, plan: dict) -> dict:
    ranked = sorted(((k, m) for k, m in modules.items() if m.get("available")), key=lambda kv: kv[1]["score"] * kv[1]["confidence"])
    bear = [f"{MODULES[k]}: {m['summary']}" for k, m in ranked if m["score"] < -0.1][:3]
    bull = [f"{MODULES[k]}: {m['summary']}" for k, m in reversed(ranked) if m["score"] > 0.1][:3]
    return {"headline": "계량 결과만으로 정리한 판단",
            "summary": f"AI 해설을 만들지 못했습니다. 종합 점수 {v['score'] * 100:+.0f}로 {v['label']} 구간입니다. 아래 모듈별 점수와 근거를 확인하세요.",
            "bull": bull, "bear": bear, "risks": v.get("notes", [])[:3],
            "triggers": [f"종가가 손절가 ${plan['stop']:,.2f} 아래로 내려가면 다시 판단", f"종가가 목표가 ${plan['target']:,.2f}를 넘으면 다시 판단"]}
