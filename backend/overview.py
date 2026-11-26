"""시장 정세 한눈에 보기 (화면용 요약): 지수, 변동성, 금리, 업종 ETF, 다른 자산의 지금 위치와 흐름.

종목 분석과 같은 시세 캐시(yf:series:*)를 쓰고, 묶은 결과는 30분 캐시한다 (공개 경로라 조회마다 yfinance 를 부르지 않게).
시장 국면 점수는 판단 엔진과 같은 verdict.regime 을 업종·베타 보정 없이 부른 값이다.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import market
import verdict as vd

INDEXES = [("SPY", "S&P 500"), ("QQQ", "나스닥 100"), ("IWM", "러셀 2000 (소형주)"), ("DIA", "다우 30")]
ASSETS = [("TLT", "미국 장기 국채"), ("GLD", "금"), ("UUP", "달러"), ("BTC-USD", "비트코인")]
SECTORS = [("XLK", "기술"), ("XLC", "커뮤니케이션"), ("XLY", "경기소비재"), ("XLF", "금융"), ("XLI", "산업재"), ("XLV", "헬스케어"),
           ("XLP", "필수소비재"), ("XLE", "에너지"), ("XLB", "소재"), ("XLU", "유틸리티"), ("XLRE", "부동산")]
TTL = 1800
SPARK_DAYS, SPARK_POINTS = 126, 63   # 최근 6개월을 63점으로
LEADERS_N = 5                         # 업종마다 대표 종목 수
HOLD_TTL = 7 * 24 * 3600              # ETF 상위 보유 종목은 자주 안 바뀐다


def stats(p: dict | None) -> dict | None:
    """한 종목의 일봉 -> 지금 가격, 기간별 수익률, 이동평균 대비 위치, 6개월 흐름."""
    c = market.close_series(p)
    if len(c) < 30:
        return None
    last = float(c.iloc[-1])
    ret = lambda n: round(last / float(c.iloc[-1 - n]) - 1, 4) if len(c) > n else None
    year = c[c.index.year == c.index[-1].year]
    prev_year = c[c.index.year < c.index[-1].year]
    sma = lambda n: float(c.tail(n).mean()) if len(c) >= n else None
    s50, s200 = sma(50), sma(200)
    tail = c.tail(SPARK_DAYS)
    step = max(1, len(tail) // SPARK_POINTS)
    return {"price": round(last, 4), "as_of": str(c.index[-1].date()), "d1": ret(1), "w1": ret(5), "m1": ret(21), "m3": ret(63), "y1": ret(252),
            "ytd": round(last / float(prev_year.iloc[-1]) - 1, 4) if len(prev_year) and len(year) else None,
            "vs_50": None if s50 is None else round(last / s50 - 1, 4), "vs_200": None if s200 is None else round(last / s200 - 1, 4),
            "from_high": round(last / float(c.tail(252).max()) - 1, 4),
            "spark": [round(float(v), 4) for v in tail.iloc[::-1].iloc[::step].iloc[::-1]]}   # 끝점(오늘)이 꼭 들어가게 뒤에서부터 고른다


def vix_word(v: float) -> str:
    return "안정" if v < 18 else "보통" if v < 25 else "높음" if v < 32 else "매우 높음"   # verdict.regime 과 같은 경계


def build(cache: market.Cache) -> dict:
    def series(sym: str) -> dict | None:
        period = "10y" if sym == "SPY" else "2y"   # 종목 분석이 쓰는 캐시 키와 맞춘다
        try:
            return market._cached(cache, f"yf:series:{sym}:{period}", market.TTL["series"], lambda: market._history(sym, period))
        except Exception as exc:  # noqa: BLE001 - 한 종목 조회 실패로 화면 전체가 죽지 않게
            market.log.warning("overview %s: %r", sym, exc)
            return None

    syms = [s for s, _ in INDEXES + ASSETS + SECTORS] + ["^VIX", "^TNX"]
    with ThreadPoolExecutor(max_workers=8) as ex:
        raw = dict(zip(syms, ex.map(series, syms)))
    if not raw.get("SPY"):
        raise LookupError("시장 데이터를 불러오지 못했습니다. 잠시 뒤 다시 시도하세요.")

    def rows(group: list[tuple[str, str]]) -> list[dict]:
        return [{"symbol": s, "name": n, **st} for s, n in group if (st := stats(raw.get(s)))]

    spy, vix, tnx = (market.close_series(raw.get(k)) for k in ("SPY", "^VIX", "^TNX"))
    sectors = rows(SECTORS)
    v = float(vix.iloc[-1]) if len(vix) else None
    return {
        "as_of": str(spy.index[-1].date()), "fetched_at": datetime.now(timezone.utc).isoformat(),
        "regime": vd.regime(spy, vix, tnx, None, None, None),
        "vix": None if v is None else {"value": round(v, 2), "word": vix_word(v), **{k: x for k, x in (stats(raw.get("^VIX")) or {}).items() if k in ("d1", "w1", "m1", "spark")}},
        "tnx": None if not len(tnx) else {"value": round(float(tnx.iloc[-1]), 3),
                                          "change_3m": round(float(tnx.iloc[-1] - tnx.iloc[-64]), 3) if len(tnx) > 64 else None},
        "indexes": rows(INDEXES), "assets": rows(ASSETS), "sectors": sectors,
        "breadth": {"total": len(sectors), "above_200": sum(1 for s in sectors if (s["vs_200"] or 0) > 0),
                    "above_50": sum(1 for s in sectors if (s["vs_50"] or 0) > 0)},
    }


def market_overview(cache: market.Cache) -> dict:
    hit = cache.get("market:overview", TTL)
    if hit is not None:
        return hit
    out = build(cache)
    cache.set("market:overview", out)
    return out


# ------------------------------------------------------------
# 업종별 대표 종목: 업종 ETF 가 가장 많이 담은 종목. 시세와 기본 지표만 (NIM·백테스트 없음, 공개 경로)
# 블로그(dongne-jiwon 머니이정표)의 미국 증시 글이 읽는다. 관심 종목 자동 판단과는 따로다

def top_unique(rows: list[dict], n: int) -> list[dict]:
    """같은 회사의 다른 주식(알파벳 Class A·C 같은)은 하나만 남기고 n 개까지"""
    out, seen = [], set()
    for r in rows:
        key = re.sub(r"\s+(class|cl\.?)\s+[a-z]\b.*$", "", r["name"].lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) == n:
            break
    return out


def _holdings(etf: str) -> list[dict] | None:
    import yfinance as yf
    th = yf.Ticker(etf).funds_data.top_holdings
    if th is None or th.empty:
        return None
    name_col = "Name" if "Name" in th.columns else None
    rows = [{"symbol": str(sym), "name": str(th.loc[sym, name_col]) if name_col else str(sym), "weight": round(float(th.loc[sym].iloc[-1]), 4)}
            for sym in th.index]
    return top_unique(rows, LEADERS_N)


def build_leaders(cache: market.Cache) -> dict:
    def hold(etf: str) -> list[dict]:
        try:
            return market._cached(cache, f"yf:holdings:{etf}", HOLD_TTL, lambda: _holdings(etf)) or []
        except Exception as exc:  # noqa: BLE001 - 한 업종을 못 받아도 나머지는 보여 준다
            market.log.warning("holdings %s: %r", etf, exc)
            return []

    def one(sym: str) -> tuple[str, dict | None, dict]:
        try:
            ser = market._cached(cache, f"yf:series:{sym}:2y", market.TTL["series"], lambda: market._history(sym, "2y"))
        except Exception as exc:  # noqa: BLE001
            market.log.warning("leaders series %s: %r", sym, exc)
            ser = None
        try:
            info = market._cached(cache, f"yf:info:{sym}", market.TTL["info"], lambda: market._info(sym)) or {}
        except Exception as exc:  # noqa: BLE001
            market.log.warning("leaders info %s: %r", sym, exc)
            info = {}
        return sym, stats(ser), info

    with ThreadPoolExecutor(max_workers=8) as ex:
        holds = dict(zip([e for e, _ in SECTORS], ex.map(hold, [e for e, _ in SECTORS])))
        syms = sorted({h["symbol"] for hs in holds.values() for h in hs})
        got = {sym: (st, info) for sym, st, info in ex.map(one, syms)}
    sectors = []
    for etf, name in SECTORS:
        rows = []
        for h in holds[etf]:
            st, info = got.get(h["symbol"], (None, {}))
            if not st:
                continue
            target = market._num(info.get("targetMeanPrice"))
            rows.append({"symbol": h["symbol"], "name": market.short_name(info, h["name"]) if info else h["name"], "weight": h["weight"],
                         **{k: st[k] for k in ("price", "as_of", "d1", "w1", "m1", "ytd", "vs_200", "from_high")},
                         "market_cap": market._num(info.get("marketCap")), "pe": market._num(info.get("trailingPE")),
                         "forward_pe": market._num(info.get("forwardPE")),
                         "target_upside": round(target / st["price"] - 1, 4) if target and st["price"] else None})
        sectors.append({"symbol": etf, "name": name, "leaders": rows})
    if not any(s["leaders"] for s in sectors):
        raise LookupError("업종 대표 종목 데이터를 불러오지 못했습니다. 잠시 뒤 다시 시도하세요.")
    return {"as_of": max(r["as_of"] for s in sectors for r in s["leaders"]), "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sectors": sectors}


def sector_leaders(cache: market.Cache) -> dict:
    hit = cache.get("market:leaders", TTL)
    if hit is not None:
        return hit
    out = build_leaders(cache)
    cache.set("market:leaders", out)
    return out
