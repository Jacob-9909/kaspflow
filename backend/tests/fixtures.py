"""테스트용 합성 데이터 (네트워크 없음). market.load_bundle 과 같은 모양의 묶음을 만든다."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd


def ohlcv(n: int = 2600, drift: float = 0.0004, vol: float = 0.015, seed: int = 1, start: str = "2016-01-04",
          ar: float = 0.0) -> pd.DataFrame:
    """평일 일봉. ar > 0 이면 수익률에 자기상관(추세)이 생긴다."""
    rng = np.random.default_rng(seed)
    eps = rng.normal(0, vol, n)
    r = np.zeros(n)
    for i in range(n):
        r[i] = drift + eps[i] + (ar * r[i - 1] if i else 0.0)
    close = 100 * np.exp(np.cumsum(r))
    gap = rng.normal(0, vol / 4, n)
    opn = close / np.exp(r) * np.exp(gap)
    hi = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    lo = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame({"Open": opn, "High": hi, "Low": lo, "Close": close,
                         "Volume": rng.integers(1_000_000, 5_000_000, n).astype(float)}, index=idx)


def to_cache(df: pd.DataFrame) -> dict:
    return {"t": [d.strftime("%Y-%m-%d") for d in df.index], "o": df["Open"].tolist(), "h": df["High"].tolist(),
            "l": df["Low"].tolist(), "c": df["Close"].tolist(), "v": df["Volume"].tolist()}


def statements() -> dict:
    dates = ["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"]
    inc = {"Total Revenue": [120e9, 110e9, 100e9, 90e9], "Gross Profit": [60e9, 54e9, 48e9, 42e9],
           "Operating Income": [30e9, 27e9, 24e9, 20e9], "EBIT": [30e9, 27e9, 24e9, 20e9],
           "Net Income": [24e9, 21e9, 19e9, 16e9], "Diluted EPS": [6.0, 5.2, 4.7, 4.0],
           "Interest Expense": [1e9, 1e9, 1.1e9, 1.2e9], "Tax Rate For Calcs": [0.18, 0.18, 0.19, 0.2],
           "Research And Development": [12e9, 11e9, 10e9, 9e9]}
    bal = {"Total Assets": [200e9, 190e9, 180e9, 170e9], "Current Assets": [80e9, 75e9, 70e9, 66e9],
           "Current Liabilities": [40e9, 40e9, 39e9, 38e9], "Long Term Debt": [30e9, 32e9, 34e9, 35e9],
           "Total Debt": [35e9, 37e9, 39e9, 40e9], "Stockholders Equity": [110e9, 100e9, 92e9, 85e9],
           "Retained Earnings": [70e9, 60e9, 52e9, 45e9], "Total Liabilities Net Minority Interest": [90e9, 90e9, 88e9, 85e9],
           "Ordinary Shares Number": [3.9e9, 3.95e9, 4.0e9, 4.05e9], "Working Capital": [40e9, 35e9, 31e9, 28e9],
           "Invested Capital": [140e9, 132e9, 125e9, 120e9], "Cash Cash Equivalents And Short Term Investments": [30e9, 28e9, 25e9, 22e9]}
    cf = {"Operating Cash Flow": [32e9, 29e9, 26e9, 22e9], "Capital Expenditure": [-6e9, -5.5e9, -5e9, -4.5e9],
          "Free Cash Flow": [26e9, 23.5e9, 21e9, 17.5e9], "Stock Based Compensation": [2e9, 1.8e9, 1.6e9, 1.4e9],
          "Depreciation And Amortization": [5e9, 4.6e9, 4.2e9, 4e9], "Repurchase Of Capital Stock": [-8e9, -7e9, -6e9, -5e9],
          "Cash Dividends Paid": [-4e9, -3.8e9, -3.6e9, -3.4e9], "Change In Working Capital": [-1e9, -0.5e9, 0.2e9, -0.3e9]}
    return {"income": {"dates": dates, "rows": inc}, "balance": {"dates": dates, "rows": bal},
            "cashflow": {"dates": dates, "rows": cf}, "q_income": {"dates": [], "rows": {}}}


def info(price: float, quote_type: str = "EQUITY") -> dict:
    return {"quoteType": quote_type, "longName": "Synthetic Corp", "shortName": "Synthetic Corp", "sector": "Technology",
            "industry": "Software", "currency": "USD", "exchange": "NMS", "marketCap": price * 3.9e9,
            "enterpriseValue": price * 3.9e9 + 5e9, "sharesOutstanding": 3.9e9, "trailingPE": price / 6.0,
            "forwardPE": price / 6.8, "priceToBook": price * 3.9e9 / 110e9, "trailingPegRatio": 1.6,
            "enterpriseToEbitda": 14.0, "returnOnEquity": 0.22, "returnOnAssets": 0.12, "grossMargins": 0.5,
            "operatingMargins": 0.25, "profitMargins": 0.2, "revenueGrowth": 0.09, "debtToEquity": 31.8,
            "currentRatio": 2.0, "totalDebt": 35e9, "totalCash": 30e9, "trailingEps": 6.0, "forwardEps": 6.8,
            "bookValue": 28.2, "beta": 1.1, "recommendationMean": 2.1, "recommendationKey": "buy",
            "numberOfAnalystOpinions": 20, "dividendRate": 1.0, "longBusinessSummary": "Synthetic Corp sells software."}


def analyst(price: float) -> dict:
    return {"targets": {"current": price, "low": price * 0.8, "high": price * 1.4, "mean": price * 1.12, "median": price * 1.1},
            "eps_revisions": {"0y": {"upLast30days": 6, "downLast30days": 2}, "+1y": {"upLast30days": 4, "downLast30days": 3}},
            "growth": {"+1y": {"stockTrend": 0.11}, "LTG": {"stockTrend": None}},
            "updown": {"up": 3, "down": 1, "init": 0, "main": 5, "recent": []}, "calendar": {"earnings_date": None}}


def news_items(n: int = 6) -> list[dict]:
    now = datetime.now(timezone.utc)
    titles = ["Synthetic Corp beats earnings estimates", "Synthetic Corp raises full-year guidance",
              "Regulators probe Synthetic Corp pricing", "Synthetic Corp stock falls 3% in early trading",
              "Analyst upgrades Synthetic Corp to buy", "Synthetic Corp launches new product line"]
    return [{"id": f"n{i}", "title": titles[i % len(titles)], "source": "Wire", "url": f"https://example.com/{i}",
             "published": (now - timedelta(days=i)).isoformat(), "summary": ""} for i in range(n)]


def bundle(symbol: str = "SYN", n: int = 2600, quote_type: str = "EQUITY", seed: int = 1) -> dict:
    df = ohlcv(n, seed=seed)
    spy = ohlcv(n, drift=0.0003, vol=0.01, seed=seed + 100)
    flat = lambda v: {**to_cache(spy), "c": [v] * n}
    price = float(df["Close"].iloc[-1])
    equity = quote_type == "EQUITY"
    return {"symbol": symbol, "prices": to_cache(df), "info": info(price, quote_type), "spy": to_cache(spy),
            "irx": flat(4.0), "vix": flat(16.0), "tnx": flat(4.3),
            "sector_etf": {"symbol": "XLK", "series": to_cache(ohlcv(n, seed=seed + 200))},
            "fin": statements() if equity else None, "analyst": analyst(price) if equity else None,
            "news": news_items(), "fetched_at": datetime.now(timezone.utc).isoformat()}


class MemCache:
    def __init__(self):
        self.d = {}

    def get(self, key, max_age_s):
        return self.d.get(key)

    def set(self, key, payload):
        self.d[key] = payload
