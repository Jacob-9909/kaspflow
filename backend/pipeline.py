"""분석 파이프라인: '검증·시뮬레이션' 한 번 실행. 진행 상황을 이벤트로 흘린다 (app.py 가 SSE 로 보낸다).

  1 data         데이터 수집 (10년 일봉, 재무제표, 애널리스트, 뉴스, SPY·VIX·금리·섹터)
  2 technical    기술적 지표 · 시장 국면 · 유사 국면 · 리스크
  3 backtest     전략 11개 x 변형 30개 백테스트 + 워크포워드 · 순열 검정 · DSR
  4 simulate     몬테카를로 2,000 경로 · 기간별 매매 계획
  5 fundamental  재무 품질 · 밸류에이션 · 컨센서스
  6 news         뉴스 감성 (NIM). 1단계 직후 시작해 2~5단계와 동시에 돈다
  7 personas     투자 대가 패널 (NIM x 6, 병렬)
  8 verdict      기간별 종합 판단 + 위원장 해설 (NIM)
LLM 단계가 실패하거나 시간을 넘기면 규칙 점수·기본 문장으로 대신하고 결과에 그 사실을 남긴다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

import backtest as bt
import fundamentals as fu
import indicators as ta
import market
import news as nw
import personas as pe
import simulation as sim
import validation as va
import verdict as vd

log = logging.getLogger("kaspflow.pipeline")
STEPS = [("data", "데이터 수집"), ("technical", "기술적 분석 · 시장 국면"), ("backtest", "전략 백테스트 · 통계 검증"),
         ("simulate", "몬테카를로 시뮬레이션"), ("fundamental", "재무 품질 · 밸류에이션"), ("news", "뉴스 감성 분석"),
         ("personas", "투자 대가 패널"), ("verdict", "종합 판단")]
NY = ZoneInfo("America/New_York")
DISCLAIMER = ("이 결과는 공개 데이터와 통계 모델로 만든 참고 자료이며 투자 권유가 아닙니다. 과거 성과와 시뮬레이션은 "
              "미래 수익을 보장하지 않습니다. 매매 판단과 책임은 본인에게 있습니다.")
LLM_TIMEOUT = {"news": 100, "personas": 110, "cio": 80}


def _ev(kind: str, **kw) -> dict:
    return {"type": kind, **kw}


def partial_bar(last: pd.Timestamp, quote_type: str) -> bool:
    """마지막 봉이 아직 진행 중인지 (장중이면 검증에서 뺀다 = 리페인팅 방지)."""
    if quote_type == "CRYPTOCURRENCY":
        return last.date() >= datetime.now(timezone.utc).date()
    now = datetime.now(NY)
    return last.date() == now.date() and now.weekday() < 5 and (now.hour, now.minute) < (16, 0)


def module_card(key: str, m: dict) -> dict:
    return {"key": key, "label": vd.MODULES[key], "available": bool(m.get("available")),
            "score": m.get("score", 0.0), "confidence": m.get("confidence", 0.0),
            "grade": vd.grade(m.get("score", 0.0)) if m.get("available") else "-", "summary": m.get("summary", "")}


def _chart(df_all: pd.DataFrame, bars: int = 504) -> dict:
    tail = df_all.tail(bars)
    c = df_all["Close"]
    s50, s200 = ta.sma(c, 50).tail(bars), ta.sma(c, 200).tail(bars)
    ts = [int(pd.Timestamp(d).timestamp()) for d in tail.index]
    line = lambda s: [[t, round(float(v), 4)] for t, v in zip(ts, s) if pd.notna(v)]
    return {"candles": [[t, round(o, 4), round(h, 4), round(l, 4), round(cl, 4), float(v)] for t, o, h, l, cl, v in
                        zip(ts, tail["Open"], tail["High"], tail["Low"], tail["Close"], tail["Volume"])],
            "sma50": line(s50), "sma200": line(s200)}


async def _timed(coro, seconds: float, fallback):
    try:
        return await asyncio.wait_for(coro, seconds)
    except Exception as exc:  # noqa: BLE001 - LLM 단계는 실패해도 분석은 끝까지 간다
        log.warning("llm stage failed: %r", exc)
        return fallback(exc)


async def run(symbol: str, horizon: str, position: dict | None, cache, llm, loader=market.load_bundle):
    started = time.monotonic()
    stamps: dict[str, float] = {}

    def step(name: str, status: str, **kw):
        if status == "running":
            stamps[name] = time.monotonic()
        elif name in stamps:
            kw["ms"] = int((time.monotonic() - stamps[name]) * 1000)
        return _ev("step", step=name, status=status, **kw)

    yield step("data", "running")
    b = await asyncio.to_thread(loader, symbol, cache)
    info = b["info"]
    qt = info.get("quoteType") or "EQUITY"
    ppy = 365 if qt == "CRYPTOCURRENCY" else 252
    df_all = market.frame(b["prices"])
    partial = partial_bar(df_all.index[-1], qt)
    df = df_all.iloc[:-1] if partial and len(df_all) > 300 else df_all
    price, prev = float(df_all["Close"].iloc[-1]), float(df_all["Close"].iloc[-2])
    spy, irx = market.close_series(b["spy"]), market.close_series(b["irx"])
    vix, tnx = market.close_series(b["vix"]), market.close_series(b["tnx"])
    sec = b.get("sector_etf") or {}
    sector = market.close_series(sec.get("series")) if sec else None
    rf_now = float(irx.iloc[-1]) / 100 if len(irx) else 0.04
    rf_10y = float(tnx.iloc[-1]) / 100 if len(tnx) else 0.045
    rf_ann = irx.reindex(df.index, method="ffill").fillna(rf_now * 100).to_numpy() / 100
    rf_d = (1 + rf_ann) ** (1 / ppy) - 1
    name = info.get("longName") or info.get("shortName") or symbol
    quote = {"symbol": symbol, "name": name, "price": round(price, 4), "change": round(price / prev - 1, 5),
             "currency": info.get("currency") or "USD", "exchange": info.get("fullExchangeName") or info.get("exchange"),
             "type": qt, "sector": info.get("sector"), "industry": info.get("industry"),
             "market_cap": info.get("marketCap"), "as_of": str(df_all.index[-1].date()), "partial_bar": partial}
    yield step("data", "done", detail=f"일봉 {len(df_all):,}개 · 기사 {len(b['news'])}건", quote=quote)

    news_task = asyncio.create_task(_timed(nw.label(b["news"], symbol, name, info.get("sector"), llm, cache),
                                           LLM_TIMEOUT["news"], lambda e: (b["news"], f"뉴스 분류 실패: {e!r}"[:200])))

    yield step("technical", "running")

    def cpu_technical():
        risk = sim.risk_profile(df, spy, ppy)
        tech = vd.technical(df, spy)
        reg = vd.regime(spy, vix, tnx, sector, sec.get("symbol"), risk["beta"])
        analogs = {h: sim.analogs(df, h, ppy) for h in sim.HORIZONS}
        return risk, tech, reg, analogs

    risk, tech, reg, analogs = await asyncio.to_thread(cpu_technical)
    yield step("technical", "done", detail=tech.get("summary", ""))

    yield step("backtest", "running")
    v = await asyncio.to_thread(va.validate_all, df, rf_d, bt.FEE_BPS, 7, ppy)
    strat_mod = {**va.strategy_score(v), "available": True}
    passed = [s for s in v["strategies"] if s["weight"] > 0]
    yield step("backtest", "done", detail=f"{v['n_trials']}개 변형 시험, 검증 통과 {len(passed)}개 전략")

    yield step("simulate", "running")
    beta = risk["beta"] if risk["beta"] is not None else (info.get("beta") or 1.0)
    drift = rf_now + max(0.0, min(2.5, beta)) * sim.ERP
    paths = await asyncio.to_thread(sim.forward_paths, df_all["Close"].to_numpy(float), drift, ppy)
    mc = sim.summarize_paths(paths, price, ppy)
    top = sorted((s for s in passed if s["grade"] in ("A", "B") and s["wf"]), key=lambda s: -s["weight"])
    kelly = top[0]["wf"]["metrics"] if top else None
    plans = {h: sim.trade_plan(price, risk["atr"], paths, h, kelly, ppy) for h in sim.HORIZONS}
    hz = mc[horizon]
    yield step("simulate", "done", detail=f"{sim.HORIZON_LABEL[horizon]} 뒤 상승 확률 {hz['p_up']:.0%}, 90% 범위 {hz['p5']:+.0%} ~ {hz['p95']:+.0%}")

    yield step("fundamental", "running")
    tail = df_all.tail(ppy)
    m = fu.build_metrics(info, b.get("fin"), price)
    m["eps_growth_fwd"] = (((b.get("analyst") or {}).get("growth") or {}).get("+1y") or {}).get("stockTrend")
    equity = qt == "EQUITY"
    qual = fu.quality(m) if equity else {"available": False, "score": 0.0, "confidence": 0.0, "summary": "주식이 아니라 재무 품질을 보지 않습니다."}
    val = (fu.valuation(m, b.get("analyst"), rf_10y, beta, float(tail["Low"].min()), float(tail["High"].max())) if equity
           else {"available": False, "score": 0.0, "confidence": 0.0, "summary": "주식이 아니라 밸류에이션을 하지 않습니다.", "football_field": []})
    cons = fu.consensus(info, b.get("analyst"), price) if equity else {"available": False, "score": 0.0, "confidence": 0.0, "summary": "애널리스트 데이터가 없습니다."}
    facts = fu.facts(m) if equity else {}
    yield step("fundamental", "done", detail=val.get("summary", ""))

    yield step("news", "running")
    items, news_err = await news_task
    sent = nw.aggregate(items)
    yield step("news", "done", detail=sent["summary"] + (f" ({news_err})" if news_err else ""))

    yield step("personas", "running")
    checks = pe.checklists(m, val, cons, tech, reg, plans[horizon], rf_10y)
    extra = "\n".join(f"{vd.MODULES[k]}: {x.get('summary', '')}" for k, x in
                      (("technical", tech), ("regime", reg), ("valuation", val), ("consensus", cons), ("sentiment", sent)))
    panel = await _timed(pe.panel(symbol, name, info.get("longBusinessSummary") or "", facts, checks, extra, llm),
                         LLM_TIMEOUT["personas"], lambda e: None)
    if panel is None:
        panel = await pe.panel(symbol, name, "", facts, checks, extra, None)
        panel["error"] = "투자 대가 의견 생성 시간이 초과돼 체크리스트 점수만 썼습니다."
    yield step("personas", "done", detail=panel["summary"])

    yield step("verdict", "running")
    earnings = ((b.get("analyst") or {}).get("calendar") or {}).get("earnings_date")
    earnings_days = (pd.Timestamp(earnings).date() - datetime.now(NY).date()).days if earnings else None
    base = {"technical": tech, "strategies": strat_mod, "sentiment": sent, "consensus": cons, "valuation": val,
            "quality": qual, "personas": panel, "regime": reg}
    verdicts = {h: vd.combine({**base, "analogs": analogs[h]}, h, risk, earnings_days) for h in sim.HORIZONS}
    sel = verdicts[horizon]
    mods = {**base, "analogs": analogs[horizon]}
    adv = vd.advice(sel, position, price, plans[horizon])
    narr_err, narr_dropped = None, 0
    if getattr(llm, "enabled", False):
        prompt = vd.cio_prompt(symbol, name, price, verdicts, horizon, mods, tech.get("levels", {}),
                               plans[horizon], panel, sent.get("drivers", []), risk, position)
        res = await _timed(llm.generate("cio", vd.CIO_SYSTEM, prompt, vd.Narrative, 3000), LLM_TIMEOUT["cio"], lambda e: e)
        if isinstance(res, Exception):
            narr_err, narrative = str(res)[:200], vd.fallback_narrative(sel, mods, plans[horizon])
        else:
            narrative, narr_dropped = vd.tidy_narrative({k: (v[:4] if isinstance(v, list) else v) for k, v in res.model_dump().items()},
                                                        sel, prompt, price, tech.get("levels", {}))
    else:
        narr_err, narrative = "LLM 미설정", vd.fallback_narrative(sel, mods, plans[horizon])
    yield step("verdict", "done", detail=f"{sim.HORIZON_LABEL[horizon]}: {sel['label']} (점수 {sel['score']:+.2f})")

    result = {
        "version": 1, "symbol": symbol, "name": name, "quote": quote, "horizon": horizon, "position": position,
        "as_of": datetime.now(timezone.utc).isoformat(), "ppy": ppy,
        "verdict": sel, "verdicts": verdicts, "advice": adv, "narrative": narrative,
        "modules": [module_card(k, mods[k]) for k in vd.MODULES],
        "technical": tech, "regime": reg, "risk": risk, "analogs": analogs,
        "validation": v, "simulation": {"summary": mc, "plans": plans, "drift": round(drift, 4), "paths": int(paths.shape[0])},
        "fundamentals": {"facts": facts, "quality": qual, "valuation": val, "consensus": cons},
        "news": {"aggregate": sent, "items": items[:40], "error": news_err},
        "personas": panel, "chart": _chart(df_all),
        "meta": {"duration_ms": int((time.monotonic() - started) * 1000), "llm_model": getattr(llm, "model", None),
                 "llm_calls": getattr(llm, "calls", None), "narrative_error": narr_err, "narrative_dropped": narr_dropped,
                 "data_fetched_at": b.get("fetched_at"), "disclaimer": DISCLAIMER},
    }
    yield _ev("result", data=market._jsonable(result))
