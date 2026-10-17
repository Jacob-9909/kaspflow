"""
백테스트 엔진 (midas-touch StockAnalyzer 이식 + 코인용 조정)
============================================================
[이 파일이 하는 일]
  OHLC 봉 배열을 받아 기술적 지표 전략(SMA/MACD/RSI/볼린저/OBV/복합)으로
  매수/매도 신호를 만들고, 수수료·손절·익절·추격손절을 반영해 체결을 시뮬레이션한다.
  결과로 성과 지표, 거래 목록, 차트에 찍을 매수/매도 이벤트를 돌려준다.

[midas-touch 와 달라진 점]
  - 수량: 주식은 정수 주식 수(int(cash // px))였지만, 코인은 소수 수량으로 전액 매수한다.
  - 리스크 기본값: midas 는 일봉 기준 손절 8% / 추격 12% 였다. 분·시간봉에선 과하므로
    기본은 '미사용(None)'이고, 거래비용만 Binance 현물 수준(편도 10bp)으로 둔다.
  - 연환산: 주식 252일 가정 대신 봉 간격으로 1년 봉 수를 계산한다(코인은 24/7).
  - annual_return 은 데이터가 수일뿐이라 의미가 없어 제외했다.
  - 마지막 봉은 아직 집계 중일 수 있어(Spark 가 계속 upsert) 시뮬레이션에서 제외한다.
    제외하지 않으면 봉이 닫히기 전에 신호가 생겼다 사라지는 '리페인팅'이 생긴다.

[체결 모델]
  fill="close"(기본, midas 와 동일): 신호가 확정된 봉의 종가로 즉시 체결한다.
  실제로는 슬리피지/다음 봉 시가 체결이 있으므로 결과는 다소 낙관적이다.
  fill="next_open": 신호는 봉 종가에서 확정하고 체결은 다음 봉 시가에서 한다(누수 방지).
  매수는 전액 투입(all-in), 포지션은 최대 1개(롱 온리).

[순수 함수]
  DB/네트워크에 의존하지 않는다. (그래서 단위 테스트가 쉽다)
"""
from __future__ import annotations

import copy
import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from news_features import NEWS_DEFAULT_PARAMS, news_features, news_stance, validate_news_params

# ------------------------------------------------------------
# 전략 메타데이터
# ------------------------------------------------------------
STRATEGY_LABELS: dict[str, str] = {
    "sma_crossover": "SMA 교차",
    "macd": "MACD",
    "rsi": "RSI",
    "bollinger": "볼린저 밴드",
    "obv": "OBV",
    "combined": "복합 전략",
    "news_sentiment": "뉴스 감정",  # 뉴스 신호가 필요하다 (run_backtest 의 news 인자). news_features.py 참고
}

# midas-touch DEFAULT_PARAMS 와 동일한 전략 기본값
DEFAULT_PARAMS: dict[str, dict[str, float]] = {
    "sma_crossover": {"short_window": 3, "long_window": 15},
    "macd": {"fast": 8, "slow": 17, "signal": 12},
    "rsi": {"window": 14, "buy_th": 45, "sell_th": 65},
    "bollinger": {"bol_window": 20},
    "obv": {"obv_window": 10},
    "combined": {},  # 하위 5개 전략의 기본 파라미터를 그대로 사용
    "news_sentiment": dict(NEWS_DEFAULT_PARAMS),
}

NEWS_STRATEGY = "news_sentiment"
# 거래가 이보다 적으면 결과가 우연일 수 있어 판단을 보류하라고 경고한다 (설계서 9절)
MIN_TRADES_FOR_VERDICT = 30

# 복합 전략: 하위 전략 '스탠스' 순강세표가 이 값 이상이면 진입, 이하(-)면 청산
COMBINED_THRESHOLD = 3

# 리스크·비용 기본값 (None = 미적용). 손절/익절/추격은 진입가·고점 대비 비율(0.02 = 2%).
DEFAULT_RISK: dict[str, float | None] = {
    "stop_loss_pct": None,
    "take_profit_pct": None,
    "trailing_stop_pct": None,
    "fee_bps": 10.0,  # 편도 0.10% (매수·매도 각각 적용)
}

MAX_FEE_BPS = 100.0
MAX_CAPITAL = 1e12
MINUTES_PER_YEAR = 365 * 24 * 60


# ------------------------------------------------------------
# 입력 검증
# ------------------------------------------------------------
def merge_params(strategy: str, overrides: dict[str, Any] | None) -> dict[str, float]:
    """전략 기본 파라미터에 사용자 값을 덮어쓰고 범위를 검증한다."""
    if strategy not in STRATEGY_LABELS:
        raise ValueError(f"지원하지 않는 전략: {strategy}")
    if strategy == NEWS_STRATEGY:
        return validate_news_params(overrides)  # 실수/음수 범위가 있어 기술 지표 전략과 검증 규칙이 다르다
    params = copy.deepcopy(DEFAULT_PARAMS[strategy])
    for key, value in (overrides or {}).items():
        if key not in params:
            raise ValueError(f"{strategy} 에 없는 파라미터: {key}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"파라미터 {key} 는 유한한 숫자여야 합니다")
        lo, hi = (1, 99) if key in ("buy_th", "sell_th") else (1, 1000)
        if not lo <= value <= hi:
            raise ValueError(f"파라미터 {key} 는 {lo}~{hi} 범위여야 합니다")
        params[key] = int(value)
    if strategy == "sma_crossover" and params["short_window"] >= params["long_window"]:
        raise ValueError("short_window 는 long_window 보다 작아야 합니다")
    if strategy == "macd" and params["fast"] >= params["slow"]:
        raise ValueError("fast 는 slow 보다 작아야 합니다")
    return params


def merge_risk(overrides: dict[str, Any] | None) -> dict[str, float | None]:
    """리스크·비용 설정을 기본값과 합치고 범위를 검증한다."""
    risk = dict(DEFAULT_RISK)
    for key, value in (overrides or {}).items():
        if key not in risk:
            raise ValueError(f"알 수 없는 리스크 설정: {key}")
        if value is None:
            risk[key] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{key} 는 유한한 숫자여야 합니다")
        if key == "fee_bps":
            if not 0 <= value <= MAX_FEE_BPS:
                raise ValueError(f"fee_bps 는 0~{MAX_FEE_BPS:g} 범위여야 합니다")
        elif not 0 < value < 1:
            raise ValueError(f"{key} 는 0 초과 1 미만 비율이어야 합니다 (예: 0.02 = 2%)")
        risk[key] = float(value)
    if risk["fee_bps"] is None:
        risk["fee_bps"] = 0.0
    return risk


def required_bars(strategy: str, params: dict[str, float]) -> int:
    """지표가 첫 유효 값을 내기까지(워밍업) 필요한 봉 수의 대략치."""
    if strategy == "sma_crossover":
        return int(params["long_window"])
    if strategy == "macd":
        return int(params["slow"] + params["signal"])
    if strategy == "rsi":
        return int(params["window"] + 1)
    if strategy == "bollinger":
        return int(params["bol_window"])
    if strategy == "obv":
        return int(params["obv_window"])
    if strategy == NEWS_STRATEGY:
        return 1  # 지표 워밍업이 없다 (뉴스가 있으면 첫 봉부터 신호 가능)
    # combined: 하위 전략 중 가장 긴 워밍업
    return max(required_bars(s, DEFAULT_PARAMS[s]) for s in _SIGNAL_FUNCS)


# ------------------------------------------------------------
# 전략 (신호 생성) — midas-touch 로직 그대로
#   Signal : 교차가 일어난 봉에서만 +1(매수) / -1(매도), 나머지 0
#   Position(스탠스) : 마지막 신호를 유지 (+1 매수 후 유지, -1 매도 후 유지, 신호 전 0)
# ------------------------------------------------------------
def _cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def _cross_down(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


def _to_signal(buy: pd.Series, sell: pd.Series) -> pd.Series:
    sig = pd.Series(0, index=buy.index, dtype=int)
    sig[buy] = 1
    sig[sell] = -1
    return sig


def _stance(signal: pd.Series) -> pd.Series:
    return signal.replace(0, np.nan).ffill().fillna(0)


def _sig_sma(df: pd.DataFrame, p: dict) -> pd.Series:
    short = df["Close"].rolling(int(p["short_window"])).mean()
    long_ = df["Close"].rolling(int(p["long_window"])).mean()
    return _to_signal(_cross_up(short, long_), _cross_down(short, long_))


def _sig_macd(df: pd.DataFrame, p: dict) -> pd.Series:
    fast = df["Close"].ewm(span=int(p["fast"]), adjust=False).mean()
    slow = df["Close"].ewm(span=int(p["slow"]), adjust=False).mean()
    macd = fast - slow
    signal_line = macd.ewm(span=int(p["signal"]), adjust=False).mean()
    return _to_signal(_cross_up(macd, signal_line), _cross_down(macd, signal_line))


def _sig_rsi(df: pd.DataFrame, p: dict) -> pd.Series:
    # midas 는 Wilder 평활이 아닌 단순 이동평균 RSI 를 사용한다 (그대로 유지).
    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    window = int(p["window"])
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = gain.rolling(window).mean() / loss.rolling(window).mean()
    rsi = 100 - (100 / (1 + rs))
    buy_th, sell_th = p["buy_th"], p["sell_th"]
    buy = (rsi > buy_th) & (rsi.shift(1) <= buy_th)
    sell = (rsi < sell_th) & (rsi.shift(1) >= sell_th)
    return _to_signal(buy, sell)


def _sig_bollinger(df: pd.DataFrame, p: dict) -> pd.Series:
    w = int(p["bol_window"])
    mid = df["Close"].rolling(w).mean()
    std = df["Close"].rolling(w).std()
    upper, lower = mid + std * 2, mid - std * 2
    close = df["Close"]
    buy = (close > lower) & (close.shift(1) < lower.shift(1))
    sell = (close < upper) & (close.shift(1) > upper.shift(1))
    return _to_signal(buy, sell)


def _sig_obv(df: pd.DataFrame, p: dict) -> pd.Series:
    close, volume = df["Close"], df["Volume"]
    direction = np.where(close > close.shift(1), volume, np.where(close < close.shift(1), -volume, 0))
    obv = pd.Series(direction, index=df.index).cumsum()
    obv_sma = obv.rolling(int(p["obv_window"])).mean()
    return _to_signal(_cross_up(obv, obv_sma), _cross_down(obv, obv_sma))


_SIGNAL_FUNCS = {
    "sma_crossover": _sig_sma,
    "macd": _sig_macd,
    "rsi": _sig_rsi,
    "bollinger": _sig_bollinger,
    "obv": _sig_obv,
}


def compute_position(df: pd.DataFrame, strategy: str, params: dict[str, float]) -> pd.Series:
    """전략별 스탠스(Position) 시리즈를 계산한다. (news_sentiment 는 뉴스가 필요해 run_backtest 가 따로 만든다)"""
    if strategy == NEWS_STRATEGY:
        raise ValueError("news_sentiment 는 run_backtest(news=...) 로만 실행할 수 있습니다")
    if strategy == "combined":
        # 교차 신호는 순간적이라 '같은 봉 동시 교차'는 거의 없다. 대신 각 하위 전략의
        # 현재 스탠스를 다수결로 합산해 3표 이상 강세면 진입, 3표 이상 약세면 청산한다.
        net = sum(_stance(fn(df, DEFAULT_PARAMS[name])) for name, fn in _SIGNAL_FUNCS.items())
        signal = pd.Series(0, index=df.index, dtype=int)
        signal[net >= COMBINED_THRESHOLD] = 1
        signal[net <= -COMBINED_THRESHOLD] = -1
        return _stance(signal)
    return _stance(_SIGNAL_FUNCS[strategy](df, params))


# ------------------------------------------------------------
# 체결 시뮬레이션 (리스크·비용 오버레이)
# ------------------------------------------------------------
FILL_MODES = ("close", "next_open")


def simulate(
    closes: np.ndarray,
    positions: np.ndarray,
    times: list[int],
    capital: float,
    risk: dict[str, float | None],
    opens: np.ndarray | None = None,
    fill: str = "close",
    reasons: list[str | None] | None = None,
) -> dict[str, Any]:
    """신호 + 리스크·비용으로 체결을 시뮬레이션한다.

    리스크 청산(손절/익절/추격손절)은 신호 청산보다 우선한다. 리스크로 청산된 뒤에는
    스탠스가 새로 +1 로 바뀔 때까지 재진입하지 않는다(즉시 재매수 방지).

    fill:
      - "close"     : 신호가 확정된 봉 i 의 종가로 즉시 체결 (midas-touch 와 동일, 기본값).
      - "next_open" : 판단은 봉 i 종가에서 하되, 체결은 봉 i+1 의 시가에서 한다. 손절·익절·추격
                      '판정'은 close 모드와 같이 봉 종가 기준이고 체결 가격만 다음 봉 시가다.
                      마지막 봉에서 낸 주문은 다음 봉이 없어 체결되지 않고 pending_order 로 보고한다.
                      거래 시각(entry_time/exit_time)과 마커 시각은 '체결된 봉'의 시각이다.
    reasons: 봉별 신호 청산 사유(없으면 "signal"). 예) news_sentiment 의 "news_expired".
    """
    if fill not in FILL_MODES:
        raise ValueError(f"fill 은 {'/'.join(FILL_MODES)} 중 하나여야 합니다")
    next_open = fill == "next_open"
    if next_open and (opens is None or len(opens) != len(closes)):
        raise ValueError("fill=next_open 에는 closes 와 같은 길이의 opens 가 필요합니다")

    sl, tp, ts = risk.get("stop_loss_pct"), risk.get("take_profit_pct"), risk.get("trailing_stop_pct")
    fee = float(risk.get("fee_bps") or 0.0) / 10000.0

    n = len(closes)
    cash = float(capital)
    qty = 0.0
    entry_px = peak_px = 0.0
    entry_i = 0

    equity = np.zeros(n)
    in_market = np.zeros(n)
    equity[0] = cash
    trades: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    pending: tuple[str, str] | None = None  # next_open 모드의 대기 주문 (side, reason)

    def do_exit(px: float, i: int, reason: str) -> None:
        nonlocal cash, qty
        net_entry = entry_px * (1 + fee)
        net_exit = px * (1 - fee)
        pnl_pct = (net_exit - net_entry) / net_entry if net_entry else 0.0
        pnl_amount = qty * (net_exit - net_entry)
        cash += qty * net_exit
        trades.append({
            "entry_time": times[entry_i],
            "exit_time": times[i],
            "entry_price": entry_px,
            "exit_price": px,
            "qty": qty,
            "pnl_pct": pnl_pct,
            "pnl_amount": pnl_amount,
            "exit_reason": reason,
            "bars_held": i - entry_i,
        })
        events.append({"time": times[i], "side": "sell", "price": px,
                       "reason": reason, "pnl_pct": pnl_pct})
        qty = 0.0

    def do_entry(px: float, i: int) -> None:
        nonlocal cash, qty, entry_px, peak_px, entry_i
        buy_qty = cash / (px * (1 + fee))
        if buy_qty > 0:
            cash = max(cash - buy_qty * px * (1 + fee), 0.0)
            qty = buy_qty
            entry_px = peak_px = px
            entry_i = i
            events.append({"time": times[i], "side": "buy", "price": px,
                           "reason": "signal", "pnl_pct": None})

    for i in range(1, n):
        # 1) next_open: 직전 봉에서 낸 주문을 이번 봉 시가에 체결한다
        if next_open and pending is not None:
            side, reason = pending
            pending = None
            open_px = float(opens[i])
            if side == "sell" and qty > 0:
                do_exit(open_px, i, reason)
            elif side == "buy" and qty == 0 and cash > 0:
                do_entry(open_px, i)

        # 2) 이번 봉 종가 기준으로 판단한다
        px = float(closes[i])
        exit_reason = None

        if qty > 0:
            peak_px = max(peak_px, px)
            ret = (px - entry_px) / entry_px if entry_px else 0.0
            draw = (px - peak_px) / peak_px if peak_px else 0.0
            if sl is not None and ret <= -sl:
                exit_reason = "stop_loss"
            elif tp is not None and ret >= tp:
                exit_reason = "take_profit"
            elif ts is not None and draw <= -ts:
                exit_reason = "trailing_stop"
            elif positions[i] == -1 and positions[i - 1] >= 0:
                exit_reason = (reasons[i] if reasons is not None else None) or "signal"

        if qty > 0 and exit_reason is not None:
            if next_open:
                pending = ("sell", exit_reason)
            else:
                do_exit(px, i, exit_reason)
        elif qty == 0 and positions[i] == 1 and positions[i - 1] <= 0 and cash > 0:
            if next_open:
                pending = ("buy", "signal")
            else:
                do_entry(px, i)

        equity[i] = cash + qty * px
        in_market[i] = 1.0 if qty > 0 else 0.0

    open_position = None
    if qty > 0:
        last = float(closes[-1])
        open_position = {
            "entry_time": times[entry_i],
            "entry_price": entry_px,
            "qty": qty,
            "unrealized_pct": (last * (1 - fee)) / (entry_px * (1 + fee)) - 1,
        }
    pending_order = None
    if pending is not None:
        pending_order = {"side": pending[0], "reason": pending[1], "signal_time": times[n - 1]}
    return {"equity": equity, "in_market": in_market, "trades": trades,
            "events": events, "open_position": open_position, "pending_order": pending_order}


# ------------------------------------------------------------
# 성과 지표
# ------------------------------------------------------------
def _num(x: float | None, digits: int = 6) -> float | None:
    """JSON 직렬화 안전: NaN/inf -> None, 그 외 반올림."""
    if x is None or not math.isfinite(x):
        return None
    return round(float(x), digits)


def compute_metrics(
    sim: dict[str, Any], closes: np.ndarray, capital: float, interval_minutes: int
) -> dict[str, Any]:
    equity = sim["equity"]
    trades = sim["trades"]

    total_return = equity[-1] / capital - 1
    buy_hold = closes[-1] / closes[0] - 1
    running_max = np.maximum.accumulate(equity)
    max_dd = float(np.min(equity / running_max - 1))

    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    win_amount = sum(t["pnl_amount"] for t in wins)
    loss_amount = abs(sum(t["pnl_amount"] for t in losses))
    if loss_amount > 0:
        profit_factor = win_amount / loss_amount
    else:
        profit_factor = 99.0 if win_amount > 0 else 0.0

    rets = pd.Series(equity).pct_change().dropna()
    std = float(rets.std()) if len(rets) > 1 else 0.0
    bars_per_year = MINUTES_PER_YEAR / interval_minutes
    sharpe = float(rets.mean() / std * math.sqrt(bars_per_year)) if std > 0 else 0.0

    exit_reasons: dict[str, int] = {}
    for t in trades:
        exit_reasons[t["exit_reason"]] = exit_reasons.get(t["exit_reason"], 0) + 1

    return {
        "total_return": _num(total_return),
        "buy_hold_return": _num(buy_hold),
        "max_drawdown": _num(max_dd),
        "final_value": _num(equity[-1], 2),
        "trade_count": len(trades),
        "win_rate": _num(len(wins) / len(trades), 4) if trades else 0.0,
        "profit_factor": _num(min(profit_factor, 99.0), 4),
        "avg_win_pct": _num(sum(t["pnl_pct"] for t in wins) / len(wins)) if wins else 0.0,
        "avg_loss_pct": _num(sum(t["pnl_pct"] for t in losses) / len(losses)) if losses else 0.0,
        "sharpe_ratio": _num(min(max(sharpe, -99.0), 99.0), 4),
        "exposure_pct": _num(float(np.mean(sim["in_market"][1:])), 4) if len(equity) > 1 else 0.0,
        "exit_reasons": exit_reasons,
    }


# ------------------------------------------------------------
# 기준선 (뉴스가 실제로 기여하는지 비교하는 용도)
# ------------------------------------------------------------
_BASELINE_KEYS = ("total_return", "max_drawdown", "trade_count", "win_rate", "sharpe_ratio")


def _pick(metrics: dict[str, Any]) -> dict[str, Any]:
    return {k: metrics[k] for k in _BASELINE_KEYS}


def _baseline_summary(df, strategy, params, times, closes, opens, fill, risk, capital, interval_minutes):
    """같은 봉·체결·비용 조건에서 다른 전략(복합)의 요약 지표."""
    positions = compute_position(df, strategy, params).to_numpy(dtype=float)
    sim = simulate(closes, positions, times, capital, risk, opens=opens, fill=fill)
    return _pick(compute_metrics(sim, closes, capital, interval_minutes))


def _buy_hold_summary(times, closes, opens, fill, risk, capital, interval_minutes):
    """단순 보유: 첫 신호 가능 봉에서 사서 끝까지 든다 (같은 체결 방식·수수료, 손절/익절은 없음)."""
    positions = np.ones(len(closes))
    positions[0] = 0.0
    bh_risk = {**risk, "stop_loss_pct": None, "take_profit_pct": None, "trailing_stop_pct": None}
    sim = simulate(closes, positions, times, capital, bh_risk, opens=opens, fill=fill)
    return _pick(compute_metrics(sim, closes, capital, interval_minutes))


# ------------------------------------------------------------
# 진입점
# ------------------------------------------------------------
def _to_epoch(value: Any) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp())
    return int(pd.Timestamp(value).timestamp())


def run_backtest(
    candles: list[dict[str, Any]],
    strategy: str,
    params: dict[str, Any] | None = None,
    initial_capital: float = 10_000.0,
    risk: dict[str, Any] | None = None,
    interval_minutes: int = 1,
    drop_last_bar: bool = True,
    fill: str = "close",
    news: list[dict[str, Any]] | None = None,
    compare: bool = False,
) -> dict[str, Any]:
    """OHLC 봉 배열로 백테스트를 실행한다.

    candles: 시간 오름차순 [{window_start, open, high, low, close, volume}, ...]
    drop_last_bar: 집계 중일 수 있는 마지막 봉을 시뮬레이션에서 제외(리페인팅 방지).
    fill: "close"(신호 봉 종가 체결, 기본) | "next_open"(다음 봉 시가 체결). simulate() 참고.
    news: news_sentiment 전략에서만 쓰는 뉴스 신호 목록 (news_features.news_features 참고). 다른 전략은 무시한다.
          None/빈 목록이면 신호가 없어 체결이 없다 (경고로 알린다).
    compare: True 면 같은 기간·같은 비용 조건의 복합 전략과 단순 보유 성과를 `baselines` 로 함께 돌려준다.
    """
    if fill not in FILL_MODES:
        raise ValueError(f"fill 은 {'/'.join(FILL_MODES)} 중 하나여야 합니다")
    merged_params = merge_params(strategy, params)
    merged_risk = merge_risk(risk)
    if not (isinstance(initial_capital, (int, float)) and math.isfinite(initial_capital)
            and 0 < initial_capital <= MAX_CAPITAL):
        raise ValueError(f"initial_capital 은 0 초과 {MAX_CAPITAL:g} 이하여야 합니다")
    if interval_minutes < 1:
        raise ValueError("interval_minutes 는 1 이상이어야 합니다")

    rows = candles[:-1] if drop_last_bar and candles else candles
    if len(rows) < 2:
        raise ValueError("백테스트에 필요한 봉이 부족합니다 (최소 2개)")

    df = pd.DataFrame({
        "Open": [float(c["open"]) for c in rows],
        "High": [float(c["high"]) for c in rows],
        "Low": [float(c["low"]) for c in rows],
        "Close": [float(c["close"]) for c in rows],
        "Volume": [float(c["volume"]) for c in rows],
    })
    times = [_to_epoch(c["window_start"]) for c in rows]

    closes = df["Close"].to_numpy(dtype=float)
    opens = df["Open"].to_numpy(dtype=float)
    warnings: list[str] = []
    reasons = None
    news_info = None
    if strategy == NEWS_STRATEGY:
        feats = news_features(times, interval_minutes, news or [], merged_params)
        combined = None
        if merged_params["confirm_combined"]:
            combined = compute_position(df, "combined", {}).to_numpy(dtype=float)
        positions, reasons = news_stance(feats["score"], feats["weight"], merged_params, combined)
        valid_bars = int((feats["weight"] >= merged_params["min_weight"]).sum())
        news_info = {"articles": len(news or []), "bars_with_signal": valid_bars}
        if not news:
            warnings.append("뉴스 신호가 없습니다. (수집·분류 전이거나 이 종목/기간에 기사가 없음)")
        elif valid_bars == 0:
            warnings.append("조건(관련도·신선도·min_weight)을 만족하는 뉴스가 있는 봉이 없습니다.")
    else:
        positions = compute_position(df, strategy, merged_params).to_numpy(dtype=float)
    sim = simulate(closes, positions, times, float(initial_capital), merged_risk,
                   opens=opens, fill=fill, reasons=reasons)
    metrics = compute_metrics(sim, closes, float(initial_capital), interval_minutes)

    if strategy == NEWS_STRATEGY and 0 < metrics["trade_count"] < MIN_TRADES_FOR_VERDICT:
        warnings.append(
            f"거래가 {metrics['trade_count']}회뿐입니다 (<{MIN_TRADES_FOR_VERDICT}). 결과가 우연일 수 있어 판단을 보류하세요."
        )
    if sim["pending_order"] is not None:
        warnings.append("마지막 봉에서 낸 주문은 다음 봉 시가가 없어 체결되지 않았습니다.")
    need = required_bars(strategy, merged_params)
    if len(rows) < need * 2:
        warnings.append(
            f"봉이 {len(rows)}개뿐이라 지표 워밍업({need}봉)을 빼면 신호 구간이 짧습니다. 봉 개수를 늘려 보세요."
        )
    if not sim["trades"] and sim["open_position"] is None:
        warnings.append("이 구간에서는 체결이 한 번도 없었습니다.")

    def _round_trade(t: dict[str, Any]) -> dict[str, Any]:
        return {
            **t,
            "entry_price": _num(t["entry_price"], 8), "exit_price": _num(t["exit_price"], 8),
            "qty": _num(t["qty"], 8), "pnl_pct": _num(t["pnl_pct"]), "pnl_amount": _num(t["pnl_amount"], 2),
        }

    baselines = None
    if compare:
        baselines = {
            "combined": _baseline_summary(df, "combined", {}, times, closes, opens, fill, merged_risk,
                                          float(initial_capital), interval_minutes),
            "buy_hold": _buy_hold_summary(times, closes, opens, fill, merged_risk,
                                          float(initial_capital), interval_minutes),
        }

    open_pos = sim["open_position"]
    return {
        "strategy": strategy,
        "label": STRATEGY_LABELS[strategy],
        "params_used": merged_params,
        "risk_used": merged_risk,
        "initial_capital": float(initial_capital),
        "interval_minutes": interval_minutes,
        "fill": fill,
        "bars": len(rows),
        "start_time": times[0],
        "end_time": times[-1],
        "metrics": metrics,
        "trades": [_round_trade(t) for t in sim["trades"]],
        "markers": [
            {**e, "price": _num(e["price"], 8), "pnl_pct": _num(e["pnl_pct"])}
            for e in sim["events"]
        ],
        "open_position": None if open_pos is None else {
            "entry_time": open_pos["entry_time"], "entry_price": _num(open_pos["entry_price"], 8),
            "qty": _num(open_pos["qty"], 8), "unrealized_pct": _num(open_pos["unrealized_pct"]),
        },
        "warnings": warnings,
        **({"news": news_info} if news_info is not None else {}),
        **({"baselines": baselines} if baselines is not None else {}),
    }
