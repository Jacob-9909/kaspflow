"""판단 엔진의 과거 성적 (부분 검증).

대형주들의 10년 일봉으로, 매달 '그 시점까지의 데이터' 만 써서 판단 엔진을 다시 돌리고 1·3·12개월 뒤 결과와 맞춰 본다.
적중 기준은 판단 기록 화면과 같다 (verdict.judge: 매수 +2% 초과, 매도 -2% 미만, 관망 ±5% 이내).

과거 시점 값이 있는 모듈만 쓴다: 기술적 지표, 과거 유사 국면, 시장 국면. 가중치는 이 셋으로 다시 나눈다 (verdict.combine).
- 재무·밸류에이션·애널리스트·뉴스·투자 대가: 과거 시점 값이 없다 (yfinance 는 지금 값과 최근 4~5년 재무제표만 준다).
- 검증된 전략 신호: 시점마다 워크포워드 검증을 다시 돌려야 해서 종목·시점 수만큼 몇 시간이 걸린다.
  ponytail: 그래서 뺐다. 넣으려면 연 1회 재검증 + 시점별 신호 계산을 따로 만들어야 한다.
생존 편향 주의: 지금의 대형주를 골랐으니 과거 수익률이 시장 전체보다 좋게 나온다. 그래서 적중률은 '항상 그 판단을
했을 때' 의 적중률(기준선)과 나란히 본다. 1년 기간은 표본 창이 겹쳐 실제 독립 표본이 적다.

  .venv/bin/python engine_backtest.py --out ../dashboard/src/engine-backtest.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import market
import simulation as sim
import verdict as vd
from verdict import judge

# 섹터별 대형주 (섹터 ETF 는 시장 국면의 업종 추세에 쓴다)
UNIVERSE = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AMD", "ORCL", "CSCO", "INTC"], "XLC": ["GOOGL", "META", "NFLX", "DIS"],
    "XLY": ["AMZN", "TSLA", "HD", "MCD", "NKE"], "XLP": ["WMT", "PG", "KO"], "XLF": ["JPM", "BAC", "V", "GS"],
    "XLV": ["JNJ", "UNH", "PFE", "LLY"], "XLE": ["XOM", "CVX"], "XLI": ["CAT", "BA"],
}
MODULES = ("technical", "analogs", "regime")
STEP = 21      # 매달 한 번
START = 504    # 2년치가 쌓인 뒤부터 (200일선, 베타, 유사 국면 표본)
CONF_BINS = [(0.0, 0.6), (0.6, 0.8), (0.8, 1.01)]   # 신뢰도 분포의 대략 하위·중간·상위


def evaluate(df: pd.DataFrame, spy: pd.Series, vix: pd.Series, tnx: pd.Series, sec: pd.Series | None, sec_sym: str | None,
             step: int = STEP, start: int = START, sym: str = "") -> list[dict]:
    """한 종목: step 봉마다 그 시점까지의 데이터로 기간별 판단을 내고 이후 수익률을 붙인다 (미래 값은 쓰지 않는다).
    x 는 모듈 신호(점수 x 신뢰도)로, 가중치 자동 조정(tuning.py)이 쓴다."""
    rows, c = [], df["Close"]
    for i in range(start, len(df), step):
        t = df.index[i]
        d = df.iloc[: i + 1]
        spy_t, vix_t, tnx_t = spy[spy.index <= t], vix[vix.index <= t], tnx[tnx.index <= t]
        sec_t = sec[sec.index <= t] if sec is not None else None
        risk = sim.risk_profile(d, spy_t)
        tech = vd.technical(d, spy_t)
        reg = vd.regime(spy_t, vix_t, tnx_t, sec_t, sec_sym, risk["beta"])
        for h, n in sim.HORIZONS.items():
            if i + n >= len(df):
                continue
            mods = {"technical": tech, "regime": reg, "analogs": sim.analogs(d, h)}
            v = vd.combine(mods, h, risk, None)
            rows.append({"h": h, "date": str(t.date()), "sym": sym, "verdict": v["verdict"], "conf": v["confidence"],
                         "ret": float(c.iloc[i + n] / c.iloc[i] - 1),
                         "x": {k: round(m["score"] * m["confidence"], 4) for k, m in mods.items() if m.get("available")}})
    return rows


def collect(cache=None, symbols: set[str] | None = None, log=print) -> tuple[list[dict], list[str]]:
    """UNIVERSE 전체(또는 symbols)의 표본. cache 가 있으면 하루 동안 다시 받지 않는다 (서버의 월간 자동 조정용)."""
    def hist(s: str):
        return market._cached(cache, f"yf:series:{s}:10y", 86400, lambda: market._history(s, "10y")) if cache else market._history(s, "10y")
    series = {s: market.close_series(hist(s)) for s in ("SPY", "^VIX", "^TNX", *UNIVERSE)}
    rows, used = [], []
    for etf, syms in UNIVERSE.items():
        for sym in syms:
            if symbols and sym not in symbols:
                continue
            p = hist(sym)
            if not p:
                log(f"skip {sym}: no data")
                continue
            got = evaluate(market.frame(p), series["SPY"], series["^VIX"], series["^TNX"], series[etf], etf, sym=sym)
            rows += got
            used.append(sym)
            log(f"{sym}: {len(got)} rows")
    return rows, used


def summarize(rows: list[dict]) -> dict:
    """기간별: 판단마다 개수, 적중률, 같은 기준으로 '항상 그 판단' 이었을 때의 적중률(기준선), 평균 수익률.
    신뢰도 구간별로는 적중률과, 그 구간 판단들의 기준선 평균을 나란히 둔다 (판단 섞임이 구간마다 달라 생 적중률은 비교가 안 된다)."""
    out = {}
    for h in sim.HORIZONS:
        r = [x for x in rows if x["h"] == h]
        if not r:
            continue
        base = {v: float(np.mean([judge(v, x["ret"]) for x in r])) for v in ("buy", "hold", "sell")}
        by = {}
        for v in ("buy", "hold", "sell"):
            xs = [x for x in r if x["verdict"] == v]
            if xs:
                by[v] = {"n": len(xs), "hit": round(float(np.mean([judge(v, x["ret"]) for x in xs])), 3), "base": round(base[v], 3),
                         "avg": round(float(np.mean([x["ret"] for x in xs])), 4)}
        conf = []
        for lo, hi in CONF_BINS:
            xs = [x for x in r if lo <= x["conf"] < hi]
            if xs:
                conf.append({"lo": lo, "hi": min(hi, 1.0), "n": len(xs),
                             "hit": round(float(np.mean([judge(x["verdict"], x["ret"]) for x in xs])), 3),
                             "base": round(float(np.mean([base[x["verdict"]] for x in xs])), 3)})
        out[h] = {"n": len(r), "avg": round(float(np.mean([x["ret"] for x in r])), 4), "by_verdict": by, "by_conf": conf}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--symbols", help="쉼표로 구분 (기본: UNIVERSE 전체)")
    a = ap.parse_args()
    rows, used = collect(symbols=set(a.symbols.split(",")) if a.symbols else None)
    span = [x["date"] for x in rows]
    w = vd.WEIGHTS
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "symbols": used,
        "start": min(span), "end": max(span), "step_days": STEP, "modules": [vd.MODULES[k] for k in MODULES],
        "weight_share": {h: round(sum(w[h][k] for k in MODULES), 2) for h in w},
        "horizons": summarize(rows),
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(json.dumps(report["horizons"], ensure_ascii=False))


if __name__ == "__main__":
    main()
