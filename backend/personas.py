"""투자 대가 패널: 6명의 투자 철학으로 같은 데이터를 본다.

각 인물은 (1) 코드가 계산하는 체크리스트 점수와 (2) NIM 이 그 인물의 관점으로 쓴 의견을 반씩 섞는다.
LLM 이 실패하면 체크리스트 점수만 쓴다. 체크리스트 기준은 virattt/ai-hedge-fund v1 (MIT) 의 결정적 점수 로직과
각 투자자의 저서·서한에 나오는 기준을 참고했다. 실제 인물의 견해가 아니라 '그 철학을 흉내 낸 규칙'이다.
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from fundamentals import _ok

PERSONAS = {
    "buffett": ("워런 버핏", "이해하기 쉬운 사업, 오래가는 경쟁우위(해자), 보수적인 부채, 꾸준한 현금 창출, "
                "안전마진이 있는 가격. 좋은 기업을 적정 가격에 사서 오래 들고 간다."),
    "graham": ("벤저민 그레이엄", "방어적 투자자 기준. 낮은 PER·PBR, 튼튼한 유동성, 꾸준한 흑자와 배당, "
               "내재가치보다 확실히 싼 가격(안전마진). 성장 기대에 돈을 내지 않는다."),
    "lynch": ("피터 린치", "합리적 가격의 성장(GARP). PEG 1 이하, 15~30% 의 지속 가능한 이익 성장, 낮은 부채. "
              "기업을 저성장·우량·고성장·경기순환·회생·자산주로 나눠 본다."),
    "munger": ("찰리 멍거", "훌륭한 기업을 적정 가격에. 높은 투하자본이익률, 가격 결정력(높은 매출총이익률), "
               "적은 설비투자, 예측 가능한 실적, 주주를 희석하지 않는 경영."),
    "wood": ("캐시 우드", "파괴적 혁신의 장기 성장. 빠른 매출 성장, 높은 연구개발 투자, 매출총이익률 확대, "
             "규모의 경제로 개선되는 수익성. 단기 밸류에이션보다 5년 뒤 시장 크기를 본다."),
    "druckenmiller": ("스탠리 드러켄밀러", "유동성과 추세를 따르는 매크로 투자. 가격 추세와 상대 강도, 이익 추정치 상향, "
                      "시장 국면, 손실은 작고 이익은 큰 비대칭 기회에 크게 건다."),
}


class View(BaseModel):
    signal: str
    confidence: int
    thesis: str
    positives: list[str]
    negatives: list[str]


def _c(name: str, passed, value: str | None = None) -> dict:
    return {"name": name, "pass": passed, "value": value}


def _pct(x):
    return None if not _ok(x) else f"{x:+.1%}"


def checklists(m: dict, val: dict, cons: dict, tech: dict, regime: dict, plan: dict, rf: float) -> dict[str, list[dict]]:
    fin = m["financial"]
    dcf = (val or {}).get("dcf") or {}
    roe_hist = m.get("roe_hist") or []
    out = {}
    if m.get("quote_type") == "EQUITY":
        out["buffett"] = [
            _c("ROE 15% 이상이 꾸준함", (sum(r >= 0.15 for r in roe_hist) / len(roe_hist) >= 0.8) if len(roe_hist) >= 3
               else (m["roe"] >= 0.15 if _ok(m["roe"]) else None), _pct(m["roe"])),
            _c("영업이익률 15% 이상, 변동 5%p 이내", (m["op_margin"] >= 0.15 and (m["op_margin_std"] or 0) <= 0.05)
               if _ok(m["op_margin"]) else None, _pct(m["op_margin"])),
            _c("부채비율 0.5배 미만", None if fin or not _ok(m["debt_to_equity"]) else m["debt_to_equity"] < 0.5,
               None if not _ok(m["debt_to_equity"]) else f"{m['debt_to_equity']:.2f}배"),
            _c("잉여현금흐름 매년 플러스", m["fcf_all_positive"]),
            _c("주식 수 유지·감소 (자사주 매입)", None if not _ok(m["share_change"]) else m["share_change"] <= 0.005,
               _pct(m["share_change"])),
            _c("오너 어닝 수익률 ≥ 국채 금리", None if not _ok(m["owner_yield"]) else m["owner_yield"] >= rf,
               _pct(m["owner_yield"])),
            _c("DCF 내재가치가 현재가보다 10% 이상 높음", None if not dcf else dcf["per_share"] >= m["price"] * 1.1,
               None if not dcf else f"${dcf['per_share']:,.2f}"),
        ]
        graham_n = (val or {}).get("models", {}).get("GrahamNumber")
        out["graham"] = [
            _c("PER 15배 이하", None if not _ok(m["pe"]) else m["pe"] <= 15, None if not _ok(m["pe"]) else f"{m['pe']:.1f}배"),
            _c("PER x PBR ≤ 22.5", None if not _ok(m["pe"], m["pb"]) else m["pe"] * m["pb"] <= 22.5),
            _c("유동비율 2배 이상", None if fin or not _ok(m["current_ratio"]) else m["current_ratio"] >= 2,
               None if not _ok(m["current_ratio"]) else f"{m['current_ratio']:.2f}배"),
            _c("장기부채 ≤ 순유동자산", None if fin or not _ok(m["lt_debt"], m["working_capital"]) else m["lt_debt"] <= m["working_capital"]),
            _c("최근 연도 모두 흑자", m["eps_all_positive"]),
            _c("배당 지급", (m["dividend_yield"] or 0) > 0, _pct(m["dividend_yield"])),
            _c("현재가 ≤ 그레이엄 넘버", None if not graham_n else m["price"] <= graham_n,
               None if not graham_n else f"${graham_n:,.2f}"),
        ]
        g = m["eps_cagr_3y"] if _ok(m["eps_cagr_3y"]) else None
        out["lynch"] = [
            _c("PEG 1.2 이하", None if not _ok(m["peg"]) or m["peg"] <= 0 else m["peg"] <= 1.2,
               None if not _ok(m["peg"]) else f"{m['peg']:.2f}"),
            _c("EPS 성장률 10~40% (지속 가능 구간)", None if g is None else 0.10 <= g <= 0.40, _pct(g)),
            _c("부채비율 0.8배 이하", None if fin or not _ok(m["debt_to_equity"]) else m["debt_to_equity"] <= 0.8),
            _c("순현금 보유", None if not _ok(m["net_cash"]) else m["net_cash"] > 0),
            _c("PER < 성장률(%) + 배당수익률(%)", None if not _ok(m["pe"]) or g is None
               else m["pe"] < g * 100 + (m["dividend_yield"] or 0) * 100),
        ]
        out["munger"] = [
            _c("ROIC 15% 이상", None if not _ok(m["roic"]) else m["roic"] >= 0.15, _pct(m["roic"])),
            _c("매출총이익률 40% 이상 (가격 결정력)", None if fin or not _ok(m["gross_margin"]) else m["gross_margin"] >= 0.40,
               _pct(m["gross_margin"])),
            _c("매출이 매년 증가", m["rev_all_up"]),
            _c("설비투자/매출 7% 이하", None if not _ok(m["capex_ratio"]) else m["capex_ratio"] <= 0.07, _pct(m["capex_ratio"])),
            _c("주식보상/매출 5% 이하", None if not _ok(m["sbc_ratio"]) else m["sbc_ratio"] <= 0.05, _pct(m["sbc_ratio"])),
            _c("FCF 수익률 3% 이상 (적정 가격)", None if fin or not _ok(m["fcf_yield"]) else m["fcf_yield"] >= 0.03, _pct(m["fcf_yield"])),
            _c("영업이익률 변동 5%p 이내 (예측 가능성)", None if m["op_margin_std"] is None else m["op_margin_std"] <= 0.05),
        ]
        fwd = m.get("eps_growth_fwd")
        out["wood"] = [
            _c("매출 성장률 20% 이상", None if not _ok(m["rev_growth_yoy"] or m["rev_cagr_3y"])
               else max(x for x in (m["rev_growth_yoy"], m["rev_cagr_3y"]) if _ok(x)) >= 0.20, _pct(m["rev_cagr_3y"])),
            _c("R&D/매출 10% 이상", None if not _ok(m["rd_ratio"]) else m["rd_ratio"] >= 0.10, _pct(m["rd_ratio"])),
            _c("매출총이익률 50% 이상 또는 개선", None if fin or not _ok(m["gross_margin"])
               else m["gross_margin"] >= 0.5 or (m["gross_margin_trend"] or 0) > 0, _pct(m["gross_margin"])),
            _c("영업이익률 개선", None if len(m["op_margin_hist"]) < 2 else m["op_margin_hist"][0] > m["op_margin_hist"][1]),
            _c("내년 EPS 성장 추정 15% 이상", None if not _ok(fwd) else fwd >= 0.15, _pct(fwd)),
            _c("혁신 섹터 (기술·통신·헬스케어)", m.get("sector") in ("Technology", "Communication Services", "Healthcare")),
        ]
    t = tech.get("detail", {})
    rel = t.get("rel_strength_6m")
    out["druckenmiller"] = [
        _c("200일선 위 + 6개월 수익 플러스", None if t.get("above_200") is None else bool(t["above_200"]) and (t.get("ret_6m") or 0) > 0,
           _pct(t.get("ret_6m"))),
        _c("SPY 대비 6개월 상대 강도 플러스", None if rel is None else rel > 0, _pct(rel)),
        _c("EPS 추정치 상향 우세 (30일)", None if (cons.get("detail") or {}).get("eps_revision_net") is None
           else cons["detail"]["eps_revision_net"] > 0),
        _c("시장(SPY) 200일선 위", regime.get("detail", {}).get("spy_above_200")),
        _c("목표가 먼저 도달 확률 ≥ 손절 먼저 도달 확률", None if not plan else plan["p_target_first"] >= plan["p_stop_first"],
           None if not plan else f"{plan['p_target_first']:.0%} vs {plan['p_stop_first']:.0%}"),
    ]
    return out


def rule_score(items: list[dict]) -> float | None:
    vals = [1.0 if c["pass"] else -1.0 for c in items if c["pass"] is not None]
    return sum(vals) / len(vals) if len(vals) >= 3 else None


def _prompt(key: str, symbol: str, name: str, facts: dict, checks: list[dict], extra: str, summary: str) -> str:
    lines = [f"- {k}: {v}" for k, v in facts.items()]
    chk = [f"- [{'통과' if c['pass'] else '미달' if c['pass'] is False else '데이터 없음'}] {c['name']}"
           + (f" ({c['value']})" if c.get("value") else "") for c in checks]
    return (f"종목: {symbol} ({name})\n사업 설명(영문 요약): {summary[:600]}\n\n[숫자]\n" + "\n".join(lines)
            + f"\n\n[{PERSONAS[key][0]} 체크리스트 결과]\n" + "\n".join(chk) + f"\n\n[그 밖의 판단 재료]\n{extra}\n\n"
            "이 투자자의 철학으로 지금 이 종목을 판단한다.\n"
            "- signal: bullish / neutral / bearish\n- confidence: 0~100\n"
            "- thesis: 그 투자자의 말투가 아닌 담백한 한국어 3문장 이내. 위에 나온 숫자만 인용한다.\n"
            "- positives, negatives: 각각 3개 이내, 한 줄씩\n"
            "위 자료에 없는 사실(신제품, 경영진 평판, 최근 뉴스 등)은 지어내지 않는다.")


async def panel(symbol: str, name: str, summary: str, facts: dict, checks: dict[str, list[dict]], extra: str, llm) -> dict:
    async def one(key: str):
        items = checks[key]
        rs = rule_score(items)
        view, err = None, None
        if getattr(llm, "enabled", False):
            try:
                v = await llm.generate(f"persona:{key}", f"너는 {PERSONAS[key][0]}의 투자 철학({PERSONAS[key][1]})으로 "
                                       "판단하는 애널리스트다. 주어진 데이터만 쓴다.",
                                       _prompt(key, symbol, name, facts, items, extra, summary), View, 2500)
                sig = v.signal.strip().lower()
                sig = sig if sig in ("bullish", "neutral", "bearish") else "neutral"
                view = {"signal": sig, "confidence": max(0, min(100, v.confidence)), "thesis": v.thesis[:600],
                        "positives": v.positives[:3], "negatives": v.negatives[:3]}
            except Exception as exc:  # noqa: BLE001 - LLM 실패 시 규칙 점수만
                err = str(exc)[:160]
        llm_s = None if view is None else {"bullish": 1, "neutral": 0, "bearish": -1}[view["signal"]] * view["confidence"] / 100
        if rs is None and llm_s is None:
            score = None
        elif rs is None:
            score = llm_s
        elif llm_s is None:
            score = rs
        else:
            score = 0.5 * rs + 0.5 * llm_s
        return key, {"name": PERSONAS[key][0], "philosophy": PERSONAS[key][1], "checklist": items,
                     "rule_score": None if rs is None else round(rs, 3), "view": view, "error": err,
                     "score": None if score is None else round(score, 3)}

    results = dict(await asyncio.gather(*[one(k) for k in checks]))
    scored = [r["score"] for r in results.values() if r["score"] is not None]
    if not scored:
        return {"available": False, "score": 0.0, "confidence": 0.0, "members": results,
                "summary": "투자 대가 기준을 적용할 데이터가 부족합니다."}
    score = sum(scored) / len(scored)
    bulls = [r["name"] for r in results.values() if (r["score"] or 0) > 0.2]
    bears = [r["name"] for r in results.values() if (r["score"] or 0) < -0.2]
    return {"available": True, "score": round(score, 3), "confidence": round(min(1.0, len(scored) / 6), 3),
            "members": results,
            "summary": f"{len(scored)}명 평균 {score * 100:+.0f}점. 긍정적: {', '.join(bulls) or '없음'}. 부정적: {', '.join(bears) or '없음'}"}
