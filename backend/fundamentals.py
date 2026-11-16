"""재무 품질 · 밸류에이션 · 애널리스트 컨센서스.

계산은 전부 코드가 하고 LLM 은 설명만 한다 ("models reason, software computes", FinRobot 원칙).
DCF 절차와 점검 항목은 anthropics/financial-services 의 dcf-model 스킬(Apache-2.0)을 따랐다:
  CAPM 자기자본비용 + 세후 부채비용의 시가 가중 WACC, 연중(mid-year) 할인, 터미널 가치 비중 점검(>75% 경고),
  WACC x 영구성장률 3x3 민감도 표 (가운데가 기준값).
투자 대가 체크리스트의 기준값은 virattt/ai-hedge-fund v1 (MIT) 의 결정적 점수 로직을 참고했다 (personas.py).

yfinance 단위 주의: debtToEquity 는 % (84 = 0.84배), dividendYield 는 버전에 따라 % 라서 쓰지 않고
dividendRate / 가격으로 직접 계산한다.
"""
from __future__ import annotations

import math
from statistics import median

ERP = 0.045         # Damodaran 내재 주식위험프리미엄 4~5% 구간
G_TERM = 0.025


def _ok(*xs) -> bool:
    return all(x is not None and isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


def ratio(a, b):
    return a / b if _ok(a, b) and b != 0 else None


def clip(x, lo, hi):
    return max(lo, min(hi, x))


class St:
    """재무제표 접근자. rows[name] = [최근, 전년, ...]."""

    def __init__(self, s: dict | None):
        s = s or {}
        self.dates = s.get("dates", [])
        self.rows = s.get("rows", {})

    def v(self, *names, i: int = 0):
        for n in names:
            row = self.rows.get(n)
            if row and i < len(row) and _ok(row[i]):
                return row[i]
        return None

    def series(self, *names) -> list:
        for n in names:
            if n in self.rows:
                return self.rows[n]
        return []

    @property
    def years(self) -> int:
        return len(self.dates)


def _cagr(new, old, years: int):
    if not _ok(new, old) or old <= 0 or new <= 0 or years <= 0:
        return None
    return (new / old) ** (1 / years) - 1


def score_band(x, bands: list[tuple[float, float]], default: float) -> float:
    """bands = [(경계, 점수)...] 큰 경계부터. x >= 경계면 그 점수."""
    for edge, s in bands:
        if x >= edge:
            return s
    return default


# ------------------------------------------------------------
# 기본 지표 모음 (persona/CIO 도 이 dict 를 쓴다)
# ------------------------------------------------------------
def build_metrics(info: dict, fin: dict | None, price: float) -> dict:
    inc, bal, cf = St((fin or {}).get("income")), St((fin or {}).get("balance")), St((fin or {}).get("cashflow"))
    info = info or {}
    m: dict = {"price": price, "sector": info.get("sector"), "industry": info.get("industry"),
               "quote_type": info.get("quoteType")}
    m["financial"] = (info.get("sector") == "Financial Services") or any(
        w in (info.get("industry") or "") for w in ("Bank", "Insurance", "Capital Markets"))
    m["market_cap"] = info.get("marketCap")
    m["shares"] = info.get("sharesOutstanding") or bal.v("Ordinary Shares Number", "Share Issued")
    m["ev"] = info.get("enterpriseValue")

    rev = [inc.v("Total Revenue", "Operating Revenue", i=i) for i in range(inc.years)]
    m["revenue"] = rev[0] if rev else info.get("totalRevenue")
    n_rev = len([r for r in rev if r is not None])
    m["rev_cagr_3y"] = _cagr(rev[0], rev[min(3, n_rev - 1)], min(3, n_rev - 1)) if n_rev >= 2 else None
    m["rev_growth_yoy"] = ratio(rev[0], rev[1]) - 1 if n_rev >= 2 and ratio(rev[0], rev[1]) else info.get("revenueGrowth")
    m["rev_all_up"] = all(_ok(a, b) and a > b for a, b in zip(rev[:-1], rev[1:])) if n_rev >= 3 else None

    eps = [inc.v("Diluted EPS", "Basic EPS", i=i) for i in range(inc.years)]
    n_eps = len([e for e in eps if e is not None])
    m["eps_cagr_3y"] = _cagr(eps[0], eps[min(3, n_eps - 1)], min(3, n_eps - 1)) if n_eps >= 2 else None
    m["eps_all_positive"] = all(_ok(e) and e > 0 for e in eps[:n_eps]) if n_eps >= 2 else None
    m["eps_ttm"] = info.get("trailingEps")
    m["eps_fwd"] = info.get("forwardEps")
    m["bvps"] = info.get("bookValue")

    m["gross_margin"] = info.get("grossMargins")
    m["op_margin"] = info.get("operatingMargins")
    m["net_margin"] = info.get("profitMargins")
    op_hist = [ratio(inc.v("Operating Income", i=i), rev[i]) for i in range(len(rev))]
    op_hist = [x for x in op_hist if x is not None]
    m["op_margin_hist"] = op_hist
    m["op_margin_std"] = (sum((x - sum(op_hist) / len(op_hist)) ** 2 for x in op_hist) / len(op_hist)) ** 0.5 if len(op_hist) >= 3 else None
    gm_hist = [ratio(inc.v("Gross Profit", i=i), rev[i]) for i in range(len(rev))]
    m["gross_margin_trend"] = (gm_hist[0] - gm_hist[-1]) if len(gm_hist) >= 2 and _ok(gm_hist[0], gm_hist[-1]) else None

    m["roe"] = info.get("returnOnEquity")
    m["roa"] = info.get("returnOnAssets")
    roe_hist = [ratio(inc.v("Net Income", i=i), bal.v("Stockholders Equity", "Common Stock Equity", i=i)) for i in range(min(inc.years, bal.years))]
    m["roe_hist"] = [x for x in roe_hist if x is not None]

    ebit = inc.v("EBIT", "Operating Income")
    tax = inc.v("Tax Rate For Calcs")
    tax = clip(tax, 0.0, 0.35) if _ok(tax) else 0.21
    m["tax_rate"] = tax
    m["ebit"] = ebit
    ebits = [inc.v("EBIT", "Operating Income", i=i) for i in range(min(3, inc.years))]
    ebits = [e for e in ebits if e is not None]
    m["ebit_norm"] = sum(ebits) / len(ebits) if ebits else None
    invested = bal.v("Invested Capital")
    m["roic"] = ratio(ebit * (1 - tax), invested) if _ok(ebit) else None

    m["total_debt"] = info.get("totalDebt") if _ok(info.get("totalDebt")) else bal.v("Total Debt")
    m["cash"] = info.get("totalCash") if _ok(info.get("totalCash")) else bal.v("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents")
    m["net_cash"] = (m["cash"] - m["total_debt"]) if _ok(m["cash"], m["total_debt"]) else None
    de = info.get("debtToEquity")
    m["debt_to_equity"] = de / 100 if _ok(de) else ratio(m["total_debt"], bal.v("Stockholders Equity"))
    m["current_ratio"] = info.get("currentRatio") or ratio(bal.v("Current Assets"), bal.v("Current Liabilities"))
    interest = inc.v("Interest Expense", "Interest Expense Non Operating")
    m["interest_coverage"] = ratio(ebit, abs(interest)) if _ok(interest) and interest != 0 else None
    m["working_capital"] = bal.v("Working Capital") or (
        bal.v("Current Assets") - bal.v("Current Liabilities") if _ok(bal.v("Current Assets"), bal.v("Current Liabilities")) else None)
    m["lt_debt"] = bal.v("Long Term Debt", "Long Term Debt And Capital Lease Obligation")

    fcf = [cf.v("Free Cash Flow", i=i) for i in range(cf.years)]
    m["fcf_hist"] = [x for x in fcf if x is not None]
    m["fcf"] = fcf[0] if fcf else info.get("freeCashflow")
    m["fcf_all_positive"] = all(x > 0 for x in m["fcf_hist"]) if len(m["fcf_hist"]) >= 2 else None
    m["cfo"] = cf.v("Operating Cash Flow")
    m["net_income"] = inc.v("Net Income")
    m["sbc"] = cf.v("Stock Based Compensation")
    capex = cf.v("Capital Expenditure")
    m["capex"] = abs(capex) if _ok(capex) else None
    m["da"] = cf.v("Depreciation And Amortization", "Depreciation Amortization Depletion") or inc.v("Reconciled Depreciation")
    m["rd_ratio"] = ratio(inc.v("Research And Development"), m["revenue"])
    m["capex_ratio"] = ratio(m["capex"], m["revenue"])
    m["sbc_ratio"] = ratio(m["sbc"], m["revenue"])
    m["fcf_margin"] = ratio(m["fcf"], m["revenue"])
    m["fcf_yield"] = ratio(m["fcf"], m["market_cap"])
    m["earnings_yield"] = ratio(ebit, m["ev"]) if _ok(m["ev"]) and m["ev"] > 0 else None
    m["cfo_to_ni"] = ratio(m["cfo"], m["net_income"]) if _ok(m["net_income"]) and m["net_income"] > 0 else None
    sh0, sh1 = bal.v("Ordinary Shares Number", "Share Issued"), bal.v("Ordinary Shares Number", "Share Issued", i=1)
    m["share_change"] = ratio(sh0, sh1) - 1 if ratio(sh0, sh1) else None
    m["buyback"] = abs(cf.v("Repurchase Of Capital Stock") or 0) or None
    m["dividends_paid"] = abs(cf.v("Cash Dividends Paid", "Common Stock Dividend Paid") or 0) or None
    owner = None
    if _ok(m["net_income"], m["da"], m["capex"]):
        wc_change = cf.v("Change In Working Capital") or 0.0
        owner = m["net_income"] + m["da"] - m["capex"] + wc_change  # 현금흐름표 부호: 운전자본 증가는 음수
    m["owner_earnings"] = owner
    m["owner_yield"] = ratio(owner, m["market_cap"])

    m["pe"] = info.get("trailingPE")
    m["pe_fwd"] = info.get("forwardPE")
    m["pb"] = info.get("priceToBook")
    m["ps"] = info.get("priceToSalesTrailing12Months")
    m["ev_ebitda"] = info.get("enterpriseToEbitda")
    m["peg"] = info.get("trailingPegRatio") or info.get("pegRatio")
    rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate")
    m["dividend_yield"] = ratio(rate, price) if _ok(rate) else (info.get("trailingAnnualDividendYield") or 0.0)
    m["payout_ratio"] = info.get("payoutRatio")
    m["beta_info"] = info.get("beta")
    m["insider_pct"] = info.get("heldPercentInsiders")
    m["institution_pct"] = info.get("heldPercentInstitutions")
    m["short_pct_float"] = info.get("shortPercentOfFloat")
    m["inc"], m["bal"], m["cf"] = inc, bal, cf
    return m


# ------------------------------------------------------------
# 품질: 피오트로스키 F, 알트만 Z, 수익성·성장·건전성
# ------------------------------------------------------------
def piotroski(m: dict) -> dict | None:
    inc, bal, cf = m["inc"], m["bal"], m["cf"]
    if inc.years < 2 or bal.years < 2:
        return None

    def ta(i):
        return bal.v("Total Assets", i=i)

    def roa(i):
        return ratio(inc.v("Net Income", i=i), ta(i + 1) or ta(i))

    def cr(i):
        return ratio(bal.v("Current Assets", i=i), bal.v("Current Liabilities", i=i))

    def lev(i):
        return ratio(bal.v("Long Term Debt", i=i) or 0.0, ta(i))

    def gm(i):
        return ratio(inc.v("Gross Profit", i=i), inc.v("Total Revenue", i=i))

    def turn(i):
        return ratio(inc.v("Total Revenue", i=i), ta(i + 1) or ta(i))

    def cmp(a, b, better="gt"):
        if a is None or b is None:
            return None
        return a > b if better == "gt" else a < b

    cfo0 = cf.v("Operating Cash Flow")
    sh0, sh1 = bal.v("Ordinary Shares Number", "Share Issued"), bal.v("Ordinary Shares Number", "Share Issued", i=1)
    checks = [
        ("ROA 양수", None if roa(0) is None else roa(0) > 0),
        ("영업현금흐름 양수", None if cfo0 is None else cfo0 > 0),
        ("ROA 개선", cmp(roa(0), roa(1))),
        ("현금흐름 > 순이익 (발생액 낮음)", None if roa(0) is None or ratio(cfo0, ta(1) or ta(0)) is None
         else ratio(cfo0, ta(1) or ta(0)) > roa(0)),
        ("장기부채 비율 감소", cmp(lev(0), lev(1), "lt") if lev(0) else (True if lev(0) == 0 else None)),
        ("유동비율 개선", cmp(cr(0), cr(1))),
        ("신주 발행 없음", None if not _ok(sh0, sh1) else sh0 <= sh1 * 1.005),
        ("매출총이익률 개선", cmp(gm(0), gm(1))),
        ("자산회전율 개선", cmp(turn(0), turn(1))),
    ]
    avail = [c for c in checks if c[1] is not None]
    if len(avail) < 6:
        return None
    return {"score": sum(1 for c in avail if c[1]), "max": len(avail),
            "checks": [{"name": n, "pass": p} for n, p in checks]}


def altman_z(m: dict) -> float | None:
    bal, inc = m["bal"], m["inc"]
    ta, tl = bal.v("Total Assets"), bal.v("Total Liabilities Net Minority Interest")
    if m["financial"] or not _ok(ta, tl, m["market_cap"], m["working_capital"]) or ta <= 0 or tl <= 0:
        return None
    re_ = bal.v("Retained Earnings") or 0.0
    ebit = m["ebit"] or 0.0
    sales = m["revenue"] or 0.0
    return 1.2 * m["working_capital"] / ta + 1.4 * re_ / ta + 3.3 * ebit / ta + 0.6 * m["market_cap"] / tl + 1.0 * sales / ta


def quality(m: dict) -> dict:
    parts: dict[str, list[float]] = {"수익성": [], "성장성": [], "재무 건전성": [], "이익의 질": []}
    if _ok(m["roe"]):
        parts["수익성"].append(score_band(m["roe"], [(0.2, 1), (0.12, 0.5), (0.0, -0.1)], -1))
    if _ok(m["roic"]):
        parts["수익성"].append(score_band(m["roic"], [(0.15, 1), (0.08, 0.4), (0.0, -0.3)], -1))
    if _ok(m["op_margin"]):
        parts["수익성"].append(score_band(m["op_margin"], [(0.2, 1), (0.1, 0.4), (0.0, -0.2)], -1))
    if _ok(m["rev_cagr_3y"]):
        parts["성장성"].append(score_band(m["rev_cagr_3y"], [(0.15, 1), (0.05, 0.5), (0.0, -0.1)], -0.8))
    if _ok(m["eps_cagr_3y"]):
        parts["성장성"].append(score_band(m["eps_cagr_3y"], [(0.15, 1), (0.05, 0.4), (0.0, -0.2)], -0.8))
    if not m["financial"]:
        if _ok(m["debt_to_equity"]):
            parts["재무 건전성"].append(score_band(-m["debt_to_equity"], [(-0.5, 1), (-1.0, 0.4), (-2.0, -0.3)], -0.8))
        if _ok(m["interest_coverage"]):
            parts["재무 건전성"].append(score_band(m["interest_coverage"], [(10, 1), (4, 0.4), (1.5, -0.4)], -1))
        if _ok(m["current_ratio"]):
            parts["재무 건전성"].append(score_band(m["current_ratio"], [(1.5, 0.6), (1.0, 0.1)], -0.6))
    z = altman_z(m)
    if z is not None:
        parts["재무 건전성"].append(1.0 if z > 2.99 else (0.0 if z > 1.81 else -1.0))
    if _ok(m["fcf_margin"]):
        parts["이익의 질"].append(score_band(m["fcf_margin"], [(0.15, 1), (0.05, 0.4), (0.0, -0.2)], -1))
    if _ok(m["cfo_to_ni"]):
        parts["이익의 질"].append(score_band(m["cfo_to_ni"], [(1.1, 0.6), (0.8, 0.1)], -0.6))
    f = piotroski(m)
    if f:
        parts["이익의 질"].append((f["score"] - f["max"] / 2) / (f["max"] / 2))
    groups = {k: round(sum(v) / len(v), 3) for k, v in parts.items() if v}
    if not groups:
        return {"available": False, "score": 0.0, "confidence": 0.0, "summary": "재무제표가 없어 품질을 평가하지 않습니다 (ETF·코인 등).",
                "groups": {}, "piotroski": None, "altman_z": None}
    n = sum(len(v) for v in parts.values())
    score = sum(groups.values()) / len(groups)
    best = max(groups, key=groups.get)
    worst = min(groups, key=groups.get)
    return {"available": True, "score": round(score, 3), "confidence": round(min(1.0, n / 10), 3),
            "groups": groups, "piotroski": f, "altman_z": None if z is None else round(z, 2),
            "summary": f"강점 {best}({groups[best]:+.2f}), 약점 {worst}({groups[worst]:+.2f})"
                       + (f", 피오트로스키 F {f['score']}/{f['max']}" if f else "")
                       + (f", 알트만 Z {z:.2f}" if z is not None else "")}


# ------------------------------------------------------------
# 밸류에이션
# ------------------------------------------------------------
def plausible_growth(m: dict, analyst: dict | None) -> tuple[float | None, list[str]]:
    cands, src = [], []
    growth = (analyst or {}).get("growth") or {}
    for k, label in (("LTG", "애널리스트 장기 성장률"), ("+1y", "애널리스트 내년 EPS 성장률")):
        g = (growth.get(k) or {}).get("stockTrend")
        if _ok(g):
            cands.append(g)
            src.append(f"{label} {g:+.1%}")
    if _ok(m["rev_cagr_3y"]):
        cands.append(m["rev_cagr_3y"])
        src.append(f"매출 3년 CAGR {m['rev_cagr_3y']:+.1%}")
    if _ok(m["eps_cagr_3y"]):
        cands.append(m["eps_cagr_3y"])
        src.append(f"EPS 3년 CAGR {m['eps_cagr_3y']:+.1%}")
    if not cands:
        return None, []
    return clip(median(cands), -0.05, 0.30), src


def wacc(m: dict, rf: float, beta: float | None) -> dict:
    raw = clip(beta if _ok(beta) else 1.0, 0.3, 3.0)
    b = 0.67 * raw + 0.33  # Blume 조정 베타 (장기적으로 1 에 수렴하는 경향 반영, 블룸버그 방식)
    re_ = rf + b * ERP
    debt, equity = m["total_debt"] or 0.0, m["market_cap"] or 0.0
    interest = m["inc"].v("Interest Expense", "Interest Expense Non Operating")
    rd = clip(abs(interest) / debt, 0.02, 0.10) if _ok(interest) and debt > 0 else rf + 0.015
    w = (equity * re_ + debt * rd * (1 - m["tax_rate"])) / (equity + debt) if equity + debt > 0 else re_
    return {"wacc": clip(w, 0.06, 0.15), "cost_of_equity": re_, "cost_of_debt": rd, "beta": b,
            "equity_weight": equity / (equity + debt) if equity + debt > 0 else 1.0}


def _fcff_base(m: dict) -> float | None:
    """기준 FCFF: 최근 2년 평균 (영업현금 + 세후이자 - 설비투자 - 주식보상). 주식보상은 실제 비용으로 뺀다."""
    cf, inc = m["cf"], m["inc"]
    vals = []
    for i in range(min(2, cf.years)):
        cfo, capex = cf.v("Operating Cash Flow", i=i), cf.v("Capital Expenditure", i=i)
        if not _ok(cfo, capex):
            continue
        interest = inc.v("Interest Expense", "Interest Expense Non Operating", i=i) or 0.0
        sbc = cf.v("Stock Based Compensation", i=i) or 0.0
        vals.append(cfo + abs(interest) * (1 - m["tax_rate"]) - abs(capex) - sbc)
    return sum(vals) / len(vals) if vals else None


def dcf_value(fcff0: float, g1: float, w: float, m: dict, g_term: float = G_TERM) -> dict:
    pv, fcf = 0.0, fcff0
    for t in range(1, 11):
        g = g1 if t <= 5 else g1 + (g_term - g1) * (t - 5) / 5
        fcf *= 1 + g
        pv += fcf / (1 + w) ** (t - 0.5)
    tv = fcf * (1 + g_term) / (w - g_term)
    pv_tv = tv / (1 + w) ** 10
    ev = pv + pv_tv
    equity = ev - (m["total_debt"] or 0.0) + (m["cash"] or 0.0)
    return {"per_share": equity / m["shares"], "ev": ev, "tv_share": pv_tv / ev if ev > 0 else None}


def valuation(m: dict, analyst: dict | None, rf: float, beta: float | None, low52: float | None, high52: float | None) -> dict:
    price = m["price"]
    g1, g_src = plausible_growth(m, analyst)
    wc = wacc(m, rf, beta) if _ok(m["market_cap"]) else None
    models, field, notes = {}, [], []

    fcff0 = _fcff_base(m) if not m["financial"] else None
    dcf = None
    if wc and _ok(fcff0, m["shares"], g1) and fcff0 > 0 and m["shares"] > 0:
        w = wc["wacc"]
        base = dcf_value(fcff0, g1, w, m)
        grid = [[round(dcf_value(fcff0, g1, w + dw, m, G_TERM + dg)["per_share"], 2) for dg in (-0.005, 0.0, 0.005)]
                for dw in (-0.01, 0.0, 0.01)]
        lo_g, hi_g = -0.3, 0.8
        if dcf_value(fcff0, hi_g, w, m)["per_share"] < price:
            implied = hi_g
        elif dcf_value(fcff0, lo_g, w, m)["per_share"] > price:
            implied = lo_g
        else:
            for _ in range(60):
                mid = (lo_g + hi_g) / 2
                lo_g, hi_g = (mid, hi_g) if dcf_value(fcff0, mid, w, m)["per_share"] < price else (lo_g, mid)
            implied = (lo_g + hi_g) / 2
        dcf = {"per_share": round(base["per_share"], 2), "fcff0": fcff0, "growth": round(g1, 4), "growth_sources": g_src,
               "wacc": round(w, 4), "cost_of_equity": round(wc["cost_of_equity"], 4), "beta": round(wc["beta"], 2),
               "terminal_growth": G_TERM, "tv_share": None if base["tv_share"] is None else round(base["tv_share"], 3),
               "sensitivity": {"wacc": [round(w - 0.01, 4), round(w, 4), round(w + 0.01, 4)],
                               "g_term": [G_TERM - 0.005, G_TERM, G_TERM + 0.005], "values": grid},
               "implied_growth": round(implied, 4)}
        if base["tv_share"] and base["tv_share"] > 0.75:
            notes.append(f"DCF 가치의 {base['tv_share']:.0%} 가 터미널 가치라 가정에 민감합니다.")
        models["DCF"] = base["per_share"]
        flat = [x for row in grid for x in row]
        field.append({"method": "DCF (WACC·영구성장 민감도)", "low": min(flat), "mid": round(base["per_share"], 2), "high": max(flat)})
    elif fcff0 is not None and fcff0 <= 0:
        notes.append("잉여현금흐름이 마이너스라 DCF 를 적용하지 않습니다.")

    if _ok(m["eps_fwd"], g1) and m["eps_fwd"] > 0 and g1 >= 0.03:  # 역성장·턴어라운드엔 PEG 가 의미 없다
        fair_pe = clip(g1 * 100, 8, 30)
        v = fair_pe * m["eps_fwd"]
        models["PEG"] = v
        field.append({"method": f"PEG=1 적정 PER {fair_pe:.0f}배", "low": round(v * 0.8, 2), "mid": round(v, 2), "high": round(v * 1.2, 2)})
    if wc and _ok(m["ebit_norm"], m["shares"]) and m["ebit_norm"] > 0 and not m["financial"]:
        def epv(w):
            return (m["ebit_norm"] * (1 - m["tax_rate"]) / w - (m["total_debt"] or 0) + (m["cash"] or 0)) / m["shares"]
        v = epv(wc["wacc"])
        models["EPV"] = v
        field.append({"method": "수익력 가치 EPV (무성장)", "low": round(epv(wc["wacc"] + 0.01), 2), "mid": round(v, 2),
                      "high": round(epv(max(wc["wacc"] - 0.01, 0.05)), 2)})
    if _ok(m["eps_ttm"], g1) and m["eps_ttm"] > 0:
        aaa = (rf + 0.008) * 100

        def graham(g):
            return m["eps_ttm"] * (8.5 + 2 * clip(g * 100, 0, 10)) * 4.4 / aaa  # 성장률은 10% 로 제한 (원식은 낙관적)
        v = graham(g1)
        models["Graham"] = v
        field.append({"method": "그레이엄 공식", "low": round(graham(g1 - 0.03), 2), "mid": round(v, 2), "high": round(graham(g1 + 0.03), 2)})
    if _ok(m["eps_ttm"], m["bvps"]) and m["eps_ttm"] > 0 and m["bvps"] > 0:
        v = math.sqrt(22.5 * m["eps_ttm"] * m["bvps"])
        models["GrahamNumber"] = v
        field.append({"method": "그레이엄 넘버", "low": round(v, 2), "mid": round(v, 2), "high": round(v, 2)})
    t = (analyst or {}).get("targets") or {}
    if _ok(t.get("low"), t.get("high"), t.get("mean")):
        field.append({"method": "애널리스트 목표가", "low": t["low"], "mid": t["mean"], "high": t["high"]})
    if _ok(low52, high52):
        field.append({"method": "52주 가격 범위", "low": round(low52, 2), "mid": round((low52 + high52) / 2, 2), "high": round(high52, 2)})

    # 중심 적정가는 성장을 반영하는 모델만 쓴다. EPV(무성장)·그레이엄 넘버는 보수적 하한이라 참고선으로만 둔다.
    weights = {"DCF": 0.5, "PEG": 0.3, "Graham": 0.2}
    avail = {k: v for k, v in models.items() if _ok(v) and v > 0}
    core = {k: v for k, v in avail.items() if k in weights}
    fair = sum(weights[k] * v for k, v in core.items()) / sum(weights[k] for k in core) if core else None

    parts: list[tuple[float, float]] = []
    if dcf:
        parts.append((0.3, clip((dcf["per_share"] / price - 1) / 0.4, -1, 1)))
        parts.append((0.25, clip((dcf["growth"] - dcf["implied_growth"]) / 0.10, -1, 1)))
    if _ok(m["peg"]) and m["peg"] > 0:
        parts.append((0.2, score_band(-m["peg"], [(-1.0, 1), (-1.5, 0.5), (-2.5, 0.0), (-4.0, -0.5)], -1)))
    if _ok(m["fcf_yield"]) and not m["financial"]:
        parts.append((0.1, score_band(m["fcf_yield"] - rf, [(0.02, 1), (0.0, 0.5), (-0.02, 0.0), (-0.035, -0.5)], -1)))
    if _ok(m["ev_ebitda"]) and m["ev_ebitda"] > 0 and not m["financial"]:
        parts.append((0.15, score_band(-m["ev_ebitda"], [(-8, 1), (-12, 0.5), (-18, 0.0), (-25, -0.5)], -1)))
    if m["financial"] and _ok(m["pb"], m["roe"]) and m["pb"] > 0:
        parts.append((0.4, clip((m["roe"] / 0.10 - m["pb"]) / 1.0, -1, 1)))  # 은행: ROE 대비 P/B
    if not parts:
        return {"available": False, "score": 0.0, "confidence": 0.0, "fair_value": None, "upside": None,
                "dcf": None, "football_field": field, "models": {}, "notes": notes,
                "summary": "밸류에이션에 쓸 재무 데이터가 부족합니다."}
    wsum = sum(w for w, _ in parts)
    score = sum(w * s for w, s in parts) / wsum
    upside = fair / price - 1 if fair else None
    summary = (f"적정가 추정 ${fair:,.2f} (현재가 대비 {upside:+.0%})" if fair else "적정가 추정 불가")
    if dcf:
        summary += f", 현재가가 가정하는 5년 성장률 {dcf['implied_growth']:+.0%} vs 현실적 성장률 {dcf['growth']:+.0%}"
    return {"available": True, "score": round(score, 3), "confidence": round(min(1.0, wsum / 0.8), 3),
            "fair_value": None if fair is None else round(fair, 2), "upside": None if upside is None else round(upside, 4),
            "dcf": dcf, "models": {k: round(v, 2) for k, v in avail.items()}, "football_field": field,
            "multiples": {k: m[k] for k in ("pe", "pe_fwd", "peg", "pb", "ps", "ev_ebitda", "fcf_yield", "earnings_yield", "dividend_yield")},
            "notes": notes, "summary": summary}


# ------------------------------------------------------------
# 애널리스트 컨센서스 (의견·목표가·EPS 추정치 변화)
# ------------------------------------------------------------
def consensus(info: dict, analyst: dict | None, price: float) -> dict:
    a = analyst or {}
    n = info.get("numberOfAnalystOpinions") or 0
    parts: list[tuple[float, float]] = []
    detail = {"analysts": n, "rec_mean": info.get("recommendationMean"), "rec_key": info.get("recommendationKey")}
    if _ok(info.get("recommendationMean")) and n:
        parts.append((0.25, clip((2.6 - info["recommendationMean"]) / 1.0, -1, 1)))
    t = a.get("targets") or {}
    if _ok(t.get("mean")) and price > 0:
        up = t["mean"] / price - 1
        detail["target_upside"] = round(up, 4)
        parts.append((0.25, clip((up - 0.08) / 0.20, -1, 1)))  # 목표가는 평균 +8% 정도 위로 치우쳐 있어 그만큼 뺀다
    rev = a.get("eps_revisions") or {}
    nets = []
    for k in ("0y", "+1y"):
        r = rev.get(k) or {}
        up, down = r.get("upLast30days"), r.get("downLast30days")
        if _ok(up, down) and up + down > 0:
            nets.append((up - down) / (up + down))
    if nets:
        detail["eps_revision_net"] = round(sum(nets) / len(nets), 3)
        parts.append((0.35, sum(nets) / len(nets)))
    ud = a.get("updown") or {}
    if ud and (ud.get("up", 0) + ud.get("down", 0)) > 0:
        detail["rating_changes_90d"] = {"up": ud["up"], "down": ud["down"]}
        parts.append((0.15, (ud["up"] - ud["down"]) / (ud["up"] + ud["down"] + 1)))
    if not parts:
        return {"available": False, "score": 0.0, "confidence": 0.0, "detail": detail, "summary": "애널리스트 데이터가 없습니다."}
    wsum = sum(w for w, _ in parts)
    score = sum(w * s for w, s in parts) / wsum
    bits = []
    if detail.get("rec_key"):
        bits.append(f"투자의견 {detail['rec_key']} (평균 {detail['rec_mean']:.2f}, {n}명)")
    if "target_upside" in detail:
        bits.append(f"목표가 괴리 {detail['target_upside']:+.0%}")
    if "eps_revision_net" in detail:
        bits.append(f"최근 30일 EPS 추정 상향-하향 {detail['eps_revision_net']:+.2f}")
    return {"available": True, "score": round(score, 3), "confidence": round(min(1.0, n / 10) * min(1.0, wsum / 0.6), 3),
            "detail": detail, "recent": (a.get("updown") or {}).get("recent", []), "summary": ", ".join(bits)}


def facts(m: dict) -> dict:
    """LLM 에 넘길 숫자 요약 (사람이 읽는 형식). 없는 값은 빠진다."""
    def pct(x):
        return None if not _ok(x) else f"{x:+.1%}"

    def num(x, unit=""):
        return None if not _ok(x) else f"{x:,.2f}{unit}"

    def big(x):
        if not _ok(x):
            return None
        for div, u in ((1e12, "조"), (1e9, "십억"), (1e6, "백만")):
            if abs(x) >= div:
                return f"${x / div:,.2f}{u}"
        return f"${x:,.0f}"

    rows = {
        "섹터/산업": f"{m.get('sector')} / {m.get('industry')}" if m.get("sector") else None,
        "시가총액": big(m["market_cap"]), "매출(최근 연도)": big(m["revenue"]),
        "매출 3년 CAGR": pct(m["rev_cagr_3y"]), "EPS 3년 CAGR": pct(m["eps_cagr_3y"]),
        "매출총이익률": None if m["financial"] else pct(m["gross_margin"]), "영업이익률": pct(m["op_margin"]), "순이익률": pct(m["net_margin"]),
        "ROE": pct(m["roe"]), "ROIC": pct(m["roic"]), "부채비율(D/E)": num(m["debt_to_equity"], "배"),
        "유동비율": num(m["current_ratio"], "배"), "이자보상배율": num(m["interest_coverage"], "배"),
        "FCF(최근 연도)": big(m["fcf"]), "FCF 수익률": pct(m["fcf_yield"]), "오너 어닝 수익률": pct(m["owner_yield"]),
        "R&D/매출": pct(m["rd_ratio"]), "설비투자/매출": pct(m["capex_ratio"]), "주식보상/매출": pct(m["sbc_ratio"]),
        "주식 수 변화(1년)": pct(m["share_change"]), "순현금": big(m["net_cash"]),
        "PER": num(m["pe"], "배"), "선행 PER": num(m["pe_fwd"], "배"), "PEG": num(m["peg"]), "PBR": num(m["pb"], "배"),
        "PSR": num(m["ps"], "배"), "EV/EBITDA": num(m["ev_ebitda"], "배"), "배당수익률": pct(m["dividend_yield"]),
        "EPS(TTM)": num(m["eps_ttm"]), "선행 EPS": num(m["eps_fwd"]), "BPS": num(m["bvps"]),
    }
    return {k: v for k, v in rows.items() if v is not None}
