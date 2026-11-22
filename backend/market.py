"""시장 데이터 수집 (yfinance + 구글 뉴스 RSS) 과 캐시.

- 가격은 배당·분할 조정가(auto_adjust=True), 10년 일봉.
- 시장 공통 시계열(SPY, ^VIX, ^TNX, ^IRX, 섹터 ETF)은 종목과 상관없이 같은 캐시를 쓴다.
- yfinance 뉴스 엔드포인트는 2026-11 현재 'YAHOO! FINANCE IS CURRENTLY DOWN' 으로 실패할 때가 있어
  구글 뉴스 RSS 를 주 소스로 쓰고 yfinance 뉴스는 되면 보탠다.
- 캐시는 db.Cache (PostgreSQL stock.cache) 를 받는다. 테스트는 dict 기반 가짜를 넣는다.
- Yahoo 데이터는 개인 용도 조건이다 (이 앱은 1인용).
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx
import numpy as np
import pandas as pd

log = logging.getLogger("kaspflow.market")

US_EXCHANGES = {"NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS", "NAS", "NYS", "CXI", "CCC"}
SECTOR_ETF = {
    "Technology": "XLK", "Financial Services": "XLF", "Healthcare": "XLV", "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP", "Energy": "XLE", "Industrials": "XLI", "Basic Materials": "XLB",
    "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC",
}
TTL = {"prices": 3600, "series": 6 * 3600, "info": 6 * 3600, "fin": 24 * 3600, "analyst": 6 * 3600,
       "news": 3600, "search": 24 * 3600, "last": 1800}
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-^=]{0,14}$")


class Cache(Protocol):
    def get(self, key: str, max_age_s: int) -> Any | None: ...
    def set(self, key: str, payload: Any) -> None: ...


class NullCache:
    def get(self, key, max_age_s):
        return None

    def set(self, key, payload):
        pass


def clean_symbol(raw: str) -> str:
    s = (raw or "").strip().upper()
    if not SYMBOL_RE.match(s):
        raise ValueError("종목 코드 형식이 올바르지 않습니다")
    return s


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _jsonable(v):
    if isinstance(v, (str, bool)) or v is None:
        return v
    if isinstance(v, (int, float, np.integer, np.floating)):
        return _num(v)
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return str(v)


MISS_TTL = 600   # 없는 종목·빈 응답은 10분만 기억한다


def _cached(cache: Cache, key: str, ttl: int, fn):
    """캐시 우선 조회. 결과가 없던 것도 잠깐 기억한다: 공개 경로에 없는 종목을 계속 넣어도 그때마다 바깥을 부르지 않게.
    짧게만 기억하는 것은 yfinance 가 잠깐 빈 응답을 줄 때 멀쩡한 종목이 오래 막히지 않게 하려는 것이다."""
    hit = cache.get(key, ttl)
    if hit is not None:
        return hit
    if cache.get(f"miss:{key}", MISS_TTL) is not None:
        return None
    val = fn()
    cache.set(key if val is not None else f"miss:{key}", val if val is not None else 1)
    return val


# ------------------------------------------------------------
# yfinance 조각들
# ------------------------------------------------------------
def _history(symbol: str, period: str) -> dict | None:
    import yfinance as yf
    h = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
    h = h.dropna(subset=["Open", "High", "Low", "Close"])
    if h.empty:
        return None
    idx = h.index.tz_localize(None) if h.index.tz is not None else h.index
    return {"t": [d.strftime("%Y-%m-%d") for d in idx], "o": h["Open"].round(6).tolist(),
            "h": h["High"].round(6).tolist(), "l": h["Low"].round(6).tolist(),
            "c": h["Close"].round(6).tolist(), "v": h["Volume"].fillna(0).astype(float).tolist()}


def frame(p: dict) -> pd.DataFrame:
    """캐시 형식 -> DataFrame(Open/High/Low/Close/Volume, 날짜 인덱스)."""
    idx = pd.to_datetime(p["t"])
    return pd.DataFrame({"Open": p["o"], "High": p["h"], "Low": p["l"], "Close": p["c"], "Volume": p["v"]},
                        index=idx, dtype=float)


def close_series(p: dict | None) -> pd.Series:
    if not p:
        return pd.Series(dtype=float)
    return pd.Series(p["c"], index=pd.to_datetime(p["t"]), dtype=float)


def _statement(df: pd.DataFrame | None) -> dict:
    if df is None or df.empty:
        return {"dates": [], "rows": {}}
    cols = sorted(df.columns, reverse=True)
    df = df[cols]
    return {"dates": [str(c.date()) for c in cols],
            "rows": {str(k): [_num(x) for x in row] for k, row in df.iterrows()}}


def _info(symbol: str) -> dict | None:
    import yfinance as yf
    info = yf.Ticker(symbol).info or {}
    if not info or (info.get("quoteType") is None and info.get("regularMarketPrice") is None):
        return None
    info.pop("companyOfficers", None)
    return _jsonable(info)


def _fin(symbol: str) -> dict:
    import yfinance as yf
    tk = yf.Ticker(symbol)
    out = {}
    for name, attr in (("income", "income_stmt"), ("balance", "balance_sheet"), ("cashflow", "cashflow"),
                       ("q_income", "quarterly_income_stmt")):
        try:
            out[name] = _statement(getattr(tk, attr))
        except Exception as exc:  # noqa: BLE001 - 일부 종목(ETF 등)은 재무제표가 없다
            log.info("fin %s %s: %r", symbol, name, exc)
            out[name] = {"dates": [], "rows": {}}
    return out


def _df_records(df: pd.DataFrame | None) -> dict:
    if df is None or getattr(df, "empty", True):
        return {}
    return {str(i): {str(k): _jsonable(v) for k, v in row.items()} for i, row in df.iterrows()}


def _analyst(symbol: str) -> dict:
    import yfinance as yf
    tk = yf.Ticker(symbol)
    out: dict[str, Any] = {}

    def grab(name, fn):
        try:
            out[name] = fn()
        except Exception as exc:  # noqa: BLE001 - 애널리스트 데이터는 없을 수 있다
            log.info("analyst %s %s: %r", symbol, name, exc)
            out[name] = None

    grab("targets", lambda: _jsonable(tk.analyst_price_targets))
    grab("recs", lambda: _jsonable(tk.recommendations.to_dict("records")) if tk.recommendations is not None else None)
    grab("eps_revisions", lambda: _df_records(tk.eps_revisions))
    grab("eps_trend", lambda: _df_records(tk.eps_trend))
    grab("growth", lambda: _df_records(tk.growth_estimates))
    grab("earnings_est", lambda: _df_records(tk.earnings_estimate))

    def updown():
        ud = tk.upgrades_downgrades
        if ud is None or ud.empty:
            return None
        idx = ud.index.tz_localize(None) if getattr(ud.index, "tz", None) is not None else ud.index
        ud = ud.set_axis(idx)
        recent = ud[ud.index >= datetime.now() - timedelta(days=90)]
        counts = recent["Action"].value_counts().to_dict()
        rows = [{"date": str(i.date()), "firm": r.get("Firm"), "to": r.get("ToGrade"), "from": r.get("FromGrade"),
                 "action": r.get("Action"), "target": _num(r.get("currentPriceTarget"))}
                for i, r in ud.head(8).iterrows()]
        return {"up": int(counts.get("up", 0)), "down": int(counts.get("down", 0)),
                "init": int(counts.get("init", 0)), "main": int(counts.get("main", 0)), "recent": rows}

    grab("updown", updown)

    def calendar():
        cal = tk.calendar or {}
        dates = cal.get("Earnings Date") or []
        return {"earnings_date": str(dates[0]) if dates else None}

    grab("calendar", calendar)

    def insiders():
        ip = tk.insider_purchases
        if ip is None or ip.empty:
            return None
        return {str(r.iloc[0]): _num(r.get("Shares")) for _, r in ip.iterrows()}

    grab("insider_6m", insiders)
    return _jsonable(out)


# ------------------------------------------------------------
# 뉴스
# ------------------------------------------------------------
_SUFFIX = re.compile(r",?\s+(inc\.?|incorporated|corporation|corp\.?|co\.?|ltd\.?|plc|holdings?|group|company|n\.v\.|s\.a\.|ag|se)$", re.I)


def short_name(info: dict, symbol: str) -> str:
    name = (info or {}).get("shortName") or (info or {}).get("longName") or symbol
    for _ in range(3):
        name = _SUFFIX.sub("", name.strip())
    return name.replace('"', "").strip() or symbol


# 판단에 도움이 안 되는 기사: 기관 보유 변동(13F) 자동 기사("Shares Sold by X LLC"), 시세·회사 소개 페이지.
# "Berkshire Hathaway sells its stake in Apple" 같은 진짜 뉴스는 남기려고 운용사 단어가 같이 있을 때만 거른다.
_HOLDER = re.compile(r"\b(acquired|purchased|bought|sold|sells|buys)\b by\b|\b(grows|reduces|trims|boosts|lifts|raises|cuts|lowers|"
                     r"increases|decreases|sells|buys|acquires|takes)\b (its |a |new )?(stake|position|holdings|shares)\b"
                     r"|\bshares (acquired|purchased|bought|sold)\b|\bposition (lifted|raised|lowered|trimmed|cut)\b", re.I)
_FIRM = re.compile(r"\b(llc|l\.p\.|advisors?|advisory|management|wealth|planning|counsel|financial|capital|partners|"
                   r"investments?|trust|securities|asset)\b", re.I)
_QUOTE_PAGE = re.compile(r"stock price,? (news|quote)|price, news, quote|stock quote|company overview", re.I)


def is_junk(title: str) -> bool:
    return bool(_QUOTE_PAGE.search(title) or (_HOLDER.search(title) and _FIRM.search(title)))


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _google_news(query: str) -> list[dict]:
    r = httpx.get("https://news.google.com/rss/search",
                  params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                  headers={"User-Agent": "Mozilla/5.0 (kaspflow)"}, timeout=15, follow_redirects=True)
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.text).iter("item"):
        title = (it.findtext("title") or "").strip()
        source = (it.findtext("source") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        try:
            published = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        out.append({"title": title, "source": source or "Google News", "url": it.findtext("link") or "",
                    "published": published.isoformat(), "summary": ""})
    return out


def _yf_news(symbol: str) -> list[dict]:
    import yfinance as yf
    out = []
    for item in yf.Ticker(symbol).news or []:
        c = item.get("content", item)
        try:
            published = pd.Timestamp(c.get("pubDate") or c.get("displayTime")).tz_convert("UTC").isoformat()
        except Exception:  # noqa: BLE001 - 형식이 자주 바뀐다
            continue
        out.append({"title": (c.get("title") or "").strip(), "source": (c.get("provider") or {}).get("displayName") or "Yahoo",
                    "url": (c.get("canonicalUrl") or {}).get("url") or "", "published": published,
                    "summary": (c.get("summary") or "")[:300]})
    return out


def fetch_news(symbol: str, info: dict, days: int = 21, limit: int = 40) -> list[dict]:
    """최근 기사 (중복 제거, 최신순 limit 개)."""
    name = short_name(info, symbol)
    items: list[dict] = []
    queries = [f'"{name}" stock when:{days}d', f"{symbol} stock when:{days}d"]
    for q in queries:
        try:
            items += _google_news(q)
        except Exception as exc:  # noqa: BLE001 - 한 소스가 죽어도 나머지로 진행
            log.warning("google news %s: %r", q, exc)
    try:
        items += _yf_news(symbol)
    except Exception as exc:  # noqa: BLE001
        log.info("yf news %s: %r", symbol, exc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x["published"], reverse=True):
        key = norm_title(it["title"])
        if not key or key in seen or is_junk(it["title"]) or datetime.fromisoformat(it["published"]) < cutoff:
            continue
        seen.add(key)
        it["id"] = hashlib.sha1(f"{symbol}|{key}".encode()).hexdigest()[:16]
        out.append(it)
    return out[:limit]


# ------------------------------------------------------------
# 묶음 로드
# ------------------------------------------------------------
def load_bundle(symbol: str, cache: Cache) -> dict:
    """분석에 필요한 데이터를 한 번에 모은다 (캐시 우선, 미스는 병렬 조회)."""
    def series(sym: str, period: str):
        return _cached(cache, f"yf:series:{sym}:{period}", TTL["series"], lambda: _history(sym, period))

    with ThreadPoolExecutor(max_workers=8) as ex:
        f_prices = ex.submit(_cached, cache, f"yf:prices:{symbol}", TTL["prices"], lambda: _history(symbol, "10y"))
        f_info = ex.submit(_cached, cache, f"yf:info:{symbol}", TTL["info"], lambda: _info(symbol))
        f_spy = ex.submit(series, "SPY", "10y")
        f_irx = ex.submit(series, "^IRX", "10y")
        f_vix = ex.submit(series, "^VIX", "2y")
        f_tnx = ex.submit(series, "^TNX", "2y")
        prices, info = f_prices.result(), f_info.result()
        if not prices or not info:
            raise LookupError(f"{symbol} 데이터를 찾지 못했습니다 (상장 폐지·오타·일시 장애일 수 있음)")
        equity = info.get("quoteType") == "EQUITY"
        etf = SECTOR_ETF.get(info.get("sector") or "")
        f_fin = ex.submit(_cached, cache, f"yf:fin:{symbol}", TTL["fin"], lambda: _fin(symbol)) if equity else None
        f_an = ex.submit(_cached, cache, f"yf:analyst:{symbol}", TTL["analyst"], lambda: _analyst(symbol)) if equity else None
        f_sec = ex.submit(series, etf, "2y") if etf else None
        f_news = ex.submit(_cached, cache, f"news:{symbol}", TTL["news"], lambda: fetch_news(symbol, info))
        return {
            "symbol": symbol, "prices": prices, "info": info,
            "spy": f_spy.result(), "irx": f_irx.result(), "vix": f_vix.result(), "tnx": f_tnx.result(),
            "sector_etf": {"symbol": etf, "series": f_sec.result()} if f_sec else None,
            "fin": f_fin.result() if f_fin else None, "analyst": f_an.result() if f_an else None,
            "news": f_news.result() or [], "fetched_at": datetime.now(timezone.utc).isoformat(),
        }


def search(query: str, cache: Cache) -> list[dict]:
    q = (query or "").strip()
    if not q or len(q) > 40:
        return []

    def run():
        import yfinance as yf
        rows = []
        for it in yf.Search(q, max_results=12, news_count=0).quotes:
            sym = it.get("symbol") or ""
            if it.get("exchange") not in US_EXCHANGES or it.get("quoteType") not in ("EQUITY", "ETF", "CRYPTOCURRENCY"):
                continue
            if "." in sym:
                continue
            rows.append({"symbol": sym, "name": it.get("longname") or it.get("shortname") or sym,
                         "type": it.get("quoteType"), "exchange": it.get("exchDisp") or it.get("exchange")})
        return rows[:8]

    return _cached(cache, f"search:{q.lower()}", TTL["search"], run) or []


def last_closes(symbols: list[str], cache: Cache) -> dict[str, float]:
    """기록 화면용 최신 종가 (종목당 30분 캐시)."""
    out, missing = {}, []
    for s in symbols:
        hit = cache.get(f"last:{s}", TTL["last"])
        if hit is not None:
            out[s] = hit
        else:
            missing.append(s)
    if missing:
        import yfinance as yf
        data = yf.download(missing, period="5d", interval="1d", auto_adjust=True, progress=False,
                           group_by="ticker", threads=True)
        for s in missing:
            try:
                col = data[s]["Close"] if len(missing) > 1 else data["Close"]
                v = _num(pd.Series(col).dropna().iloc[-1])
            except Exception:  # noqa: BLE001 - 상장 폐지 등
                v = None
            if v is not None:
                out[s] = v
                cache.set(f"last:{s}", v)
    return out


def quote(symbol: str, cache: Cache) -> dict:
    """분석 전 화면용: 이름·가격·1년 캔들 (가격·정보 캐시를 분석과 같이 쓴다)."""
    prices = _cached(cache, f"yf:prices:{symbol}", TTL["prices"], lambda: _history(symbol, "10y"))
    info = _cached(cache, f"yf:info:{symbol}", TTL["info"], lambda: _info(symbol))
    if not prices or not info:
        raise LookupError(f"{symbol} 데이터를 찾지 못했습니다")
    full = frame(prices)
    df = full.tail(260)
    c = df["Close"]
    ts = [int(t.timestamp()) for t in df.index]

    def line(n: int) -> list:
        s = full["Close"].rolling(n, min_periods=n).mean().tail(260)
        return [[t, round(float(v), 4)] for t, v in zip(ts, s) if pd.notna(v)]
    return {"symbol": symbol, "name": info.get("longName") or info.get("shortName") or symbol,
            "type": info.get("quoteType"), "exchange": info.get("fullExchangeName") or info.get("exchange"),
            "sector": info.get("sector"), "industry": info.get("industry"), "currency": info.get("currency") or "USD",
            "market_cap": info.get("marketCap"), "price": float(c.iloc[-1]), "change": float(c.iloc[-1] / c.iloc[-2] - 1),
            "high52": float(df["High"].max()), "low52": float(df["Low"].min()), "as_of": str(df.index[-1].date()),
            "summary": (info.get("longBusinessSummary") or "")[:400],
            "candles": [[t, round(o, 4), round(h, 4), round(l, 4), round(cl, 4), float(v)]
                        for t, o, h, l, cl, v in zip(ts, df["Open"], df["High"], df["Low"], df["Close"], df["Volume"])],
            "sma50": line(50), "sma200": line(200)}
