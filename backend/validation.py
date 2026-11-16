"""전략 검증기: '이 종목에서 이 전략 신호를 믿어도 되나?'

1) 전 구간 백테스트 (파라미터 변형별). 가장 좋은 변형은 과최적화돼 있으니 참고만 한다.
2) 워크포워드: 직전 3년(IS)에서 샤프가 가장 높은 변형을 골라 다음 1년(OOS)에 그대로 쓴다. 1년씩 밀며 반복하고
   OOS 구간만 이어 붙인 성과를 '실전에 가까운 성과'로 본다. 기록이 짧으면 60/40 한 번 분할.
3) 몬테카를로 순열 검정: OOS 보유 배열을 시간축으로 원형 이동시켜(보유 기간 구조·노출 비율은 그대로) 같은
   샤프가 우연히 나올 확률(p값)을 구한다. 타이밍 실력만 따로 본다. 하루짜리(day) 전략은 거래 수익 부호를
   무작위로 뒤집는 부트스트랩으로 '평균 거래 수익 > 0' 을 검정한다.
4) DSR (Bailey & López de Prado 2014): 이번에 시험한 변형 수만큼 '최고 샤프의 기대치'를 기준선으로 놓고
   그보다 진짜로 높을 확률. 여러 개를 시험하면 하나쯤은 좋아 보이는 문제를 보정한다.
5) 거래 재표본: OOS 거래를 복원 추출로 섞어 최종 수익과 최대 낙폭의 5/50/95% 분위수를 낸다.

등급과 가중치(판단 엔진이 신호에 곱함)
  A 1.0 : p<=0.05, OOS 샤프>=0.5, OOS 거래>=15, DSR>=0.8
  B 0.6 : p<=0.10, OOS 샤프>=0.3, OOS 거래>=8,  DSR>=0.5
  C 0.25: p<=0.25, OOS 샤프>0,    OOS 거래>=5
  F 0   : 그 외 (신호를 판단에 쓰지 않는다)
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

import backtest as bt
import strategies as st

N01 = NormalDist()
EULER = 0.5772156649015329
IS_DAYS, OOS_DAYS, WARMUP = 756, 252, 252
N_PERM, N_BOOT = 1000, 1000
GRADES = (("A", 1.0, 0.05, 0.5, 15, 0.8), ("B", 0.6, 0.10, 0.3, 8, 0.5), ("C", 0.25, 0.25, 0.0, 5, None))


def _per_period_sr(r: np.ndarray, rf_d: np.ndarray) -> float:
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    return float((r - rf_d).mean() / sd) if sd > 0 else 0.0


def psr(r: np.ndarray, rf_d: np.ndarray, sr_star: float = 0.0) -> float:
    """Probabilistic Sharpe Ratio (per-period 단위). 왜도·첨도를 반영한다."""
    t = len(r)
    if t < 30:
        return 0.0
    ex = r - rf_d
    sd = ex.std(ddof=1)
    if sd == 0:
        return 0.0
    sr = ex.mean() / sd
    z = (ex - ex.mean()) / sd
    skew, kurt = float(np.mean(z ** 3)), float(np.mean(z ** 4))
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return 0.0
    return N01.cdf((sr - sr_star) * math.sqrt(t - 1) / math.sqrt(denom))


def expected_max_sr(sr_var: float, n_trials: int) -> float:
    """N개 시행 중 최고 샤프의 기대값 (귀무가설: 모든 전략의 진짜 샤프 = 0)."""
    if n_trials < 2 or sr_var <= 0:
        return 0.0
    return math.sqrt(sr_var) * ((1 - EULER) * N01.inv_cdf(1 - 1 / n_trials)
                                + EULER * N01.inv_cdf(1 - 1 / (n_trials * math.e)))


def _folds(n: int, ppy: int = 252) -> list[tuple[int, int, int]]:
    """[(is_start, oos_start, oos_end)] 인덱스. 데이터가 짧으면 60/40 한 번. 길이는 1년 봉 수(ppy)에 맞춘다."""
    is_len, oos_len = IS_DAYS * ppy // 252, OOS_DAYS * ppy // 252
    warm = min(WARMUP * ppy // 252, n // 5)
    usable = n - warm
    if usable < 250:
        return []
    if usable < is_len + oos_len:
        cut = warm + int(usable * 0.6)
        return [(warm, cut, n)]
    out, s = [], warm
    while s + is_len < n:
        out.append((s, s + is_len, min(s + is_len + oos_len, n)))
        s += oos_len
    return out


def _approx_returns(held: np.ndarray, mkt: np.ndarray, rf_d: np.ndarray, fee: float) -> np.ndarray:
    """순열 검정용 근사: 종가 기준 보유 x 시장 수익 + 현금 금리 - 회전 비용 (행렬도 받는다)."""
    turnover = np.abs(np.diff(held, axis=-1, prepend=held[..., :1]))
    return held * mkt + (1 - held) * rf_d - fee * turnover


def permutation_p(held: np.ndarray, mkt: np.ndarray, rf_d: np.ndarray, fee: float, rng) -> float | None:
    n = len(held)
    if n < 120 or held.min() == held.max():
        return None
    actual = _per_period_sr(_approx_returns(held, mkt, rf_d, fee), rf_d)
    shifts = rng.integers(20, n - 20, size=N_PERM)
    idx = (np.arange(n)[None, :] - shifts[:, None]) % n
    sims = _approx_returns(held[idx], mkt[None, :], rf_d[None, :], fee)
    sd = sims.std(axis=1, ddof=1)
    sr = np.divide((sims - rf_d[None, :]).mean(axis=1), sd, out=np.zeros(N_PERM), where=sd > 0)
    return float((1 + np.sum(sr >= actual)) / (N_PERM + 1))


def random_day_p(trade_rets: np.ndarray, baseline: np.ndarray, rng) -> float | None:
    """하루짜리 전략: 같은 수의 '아무 날 시가 매수 -> 다음 날 시가 매도' 평균보다 나을 확률의 반대(p).
    부호 뒤집기 검정은 상승 추세 종목에서 아무 날이나 사도 평균이 플러스라 실력을 과대평가한다."""
    if len(trade_rets) < 10 or len(baseline) < 30:
        return None
    sims = rng.choice(baseline, size=(N_PERM, len(trade_rets)), replace=True).mean(axis=1)
    return float((1 + np.sum(sims >= trade_rets.mean())) / (N_PERM + 1))


def trade_bootstrap(trade_rets: np.ndarray, rng) -> dict | None:
    if len(trade_rets) < 5:
        return None
    sims = rng.choice(trade_rets, size=(N_BOOT, len(trade_rets)), replace=True)
    eq = np.cumprod(1 + sims, axis=1)
    final = eq[:, -1] - 1
    mdd = (eq / np.maximum.accumulate(np.maximum(eq, 1.0), axis=1) - 1).min(axis=1)
    q = lambda a, p: round(float(np.percentile(a, p)), 4)
    return {"ret_p5": q(final, 5), "ret_p50": q(final, 50), "ret_p95": q(final, 95),
            "mdd_p5": q(mdd, 5), "mdd_p50": q(mdd, 50)}


def _grade(p, oos_sharpe, oos_trades, dsr) -> tuple[str, float]:
    if p is None:
        return "F", 0.0
    for name, w, p_max, sr_min, n_min, dsr_min in GRADES:
        ok = p <= p_max and oos_trades >= n_min and (oos_sharpe > sr_min if sr_min == 0 else oos_sharpe >= sr_min)
        if ok and (dsr_min is None or (dsr is not None and dsr >= dsr_min)):
            return name, w
    return "F", 0.0


def _vote(family: str, decision: float) -> float:
    if family == "day":
        return 0.0
    if decision >= 0.5:
        return 1.0
    return -1.0 if family == "trend" else 0.0


def _markers(trades: list[dict], since: pd.Timestamp, limit: int = 60) -> list[dict]:
    out = []
    for t in trades:
        if t["entry"] < since:
            continue
        out.append({"time": int(t["entry"].timestamp()), "side": "buy", "price": round(t["entry_px"], 4)})
        if not t["open"]:
            out.append({"time": int(t["exit"].timestamp()), "side": "sell", "price": round(t["exit_px"], 4),
                        "ret": round(t["ret"], 4)})
    return out[-limit:]


def validate_all(df: pd.DataFrame, rf_d: np.ndarray, fee_bps: float = bt.FEE_BPS, seed: int = 7, ppy: int = 252) -> dict:
    """모든 전략 x 파라미터를 백테스트·검증한다. df 는 완성된 봉만 (장중 봉 제외)."""
    rng = np.random.default_rng(seed)
    fee = fee_bps / 10_000
    n = len(df)
    dates = df.index
    close, opn = df["Close"].to_numpy(float), df["Open"].to_numpy(float)
    mkt = np.zeros(n)
    mkt[1:] = close[1:] / close[:-1] - 1
    open_to_open = np.full(n, np.nan)
    open_to_open[:-1] = opn[1:] * (1 - fee) / (opn[:-1] * (1 + fee)) - 1
    folds = _folds(n, ppy)
    bh = bt.buy_hold(df, rf_d, fee_bps)

    runs: dict[str, list[dict]] = {}
    trial_srs: list[float] = []
    for s in st.STRATEGIES:
        runs[s.key] = [bt.run(s, df, p, rf_d, fee_bps) for p in s.grid]
        trial_srs += [_per_period_sr(r["returns"], rf_d) for r in runs[s.key]]
    sr0 = expected_max_sr(float(np.var(trial_srs, ddof=1)) if len(trial_srs) > 1 else 0.0, len(trial_srs))

    oos_mask = np.zeros(n, dtype=bool)
    for _, a, b in folds:
        oos_mask[a:b] = True
    bh_oos = bh["returns"][oos_mask]
    rf_oos = rf_d[oos_mask]
    bh_oos_metrics = bt.metrics(bh_oos, rf_oos, bh["held"][oos_mask], [], ppy) if folds else None

    results = []
    for s in st.STRATEGIES:
        variants = runs[s.key]
        full = [bt.metrics(v["returns"], rf_d, v["held"], v["trades"], ppy) for v in variants]
        best_i = int(np.argmax([m["sharpe"] for m in full]))

        fold_rows, r_parts, h_parts, trade_rets = [], [], [], []
        for a0, a, b in folds:
            is_sr = [bt.sharpe(v["returns"][a0:a], rf_d[a0:a], ppy) for v in variants]
            k = int(np.argmax(is_sr))
            v = variants[k]
            r_parts.append(v["returns"][a:b])
            h_parts.append(v["held"][a:b])
            in_oos = [t for t in v["trades"] if dates[a] <= t["entry"] < (dates[b] if b < n else dates[-1] + pd.Timedelta(days=1))]
            trade_rets += [t["ret"] for t in in_oos if not t["open"]]
            fold_rows.append({"oos_start": str(dates[a].date()), "oos_end": str(dates[b - 1].date()),
                              "params": s.grid[k], "is_sharpe": round(is_sr[k], 3),
                              "oos_sharpe": round(bt.sharpe(v["returns"][a:b], rf_d[a:b], ppy), 3)})
        wf = None
        p_val = None
        mc = None
        if folds:
            r_oos, h_oos = np.concatenate(r_parts), np.concatenate(h_parts)
            wf_metrics = bt.metrics(r_oos, rf_oos, h_oos, [{"ret": x, "open": False, "days": 0} for x in trade_rets], ppy)
            is_mean = float(np.mean([f["is_sharpe"] for f in fold_rows]))
            oos_mean = float(np.mean([f["oos_sharpe"] for f in fold_rows]))
            wf = {"folds": fold_rows, "metrics": wf_metrics,
                  "efficiency": round(oos_mean / is_mean, 3) if is_mean > 0 else None,
                  "equity": bt.equity_curve(r_oos, dates[oos_mask]),
                  "bh_equity": bt.equity_curve(bh_oos, dates[oos_mask])}
            tr = np.array(trade_rets)
            if s.kind == "breakout":
                base = open_to_open[oos_mask]
                p_val = random_day_p(tr, base[~np.isnan(base)], rng)
            else:
                p_val = permutation_p(h_oos, mkt[oos_mask], rf_oos, fee, rng)
            mc = trade_bootstrap(tr, rng)

        best = variants[best_i]
        dsr = round(psr(best["returns"], rf_d, sr0), 3)
        oos_sr = wf["metrics"]["sharpe"] if wf else 0.0
        oos_n = wf["metrics"]["trades"] if wf else 0
        grade, weight = _grade(p_val, oos_sr or 0.0, oos_n, dsr)

        # 지금 쓸 변형: 가장 최근 IS 길이 구간에서 샤프가 가장 높은 것 (워크포워드 트레이더가 오늘 고를 값)
        recent = slice(max(0, n - IS_DAYS * ppy // 252), n)
        cur_i = int(np.argmax([bt.sharpe(v["returns"][recent], rf_d[recent], ppy) for v in variants]))
        cur = variants[cur_i]
        series = cur["series"]
        if s.kind == "breakout":
            decision, fresh, since = 0.0, False, None
        else:
            sv = series.to_numpy(float)
            decision = float(sv[-1])
            changed = np.where(np.diff(sv) != 0)[0]
            since = int(n - 1 - changed[-1]) if len(changed) else None
            fresh = since is not None and since <= 2
        results.append({
            "key": s.key, "label": s.label, "family": s.family, "origin": s.origin, "rule": s.rule,
            "variants": len(variants),
            "best_full": {"params": s.grid[best_i], "metrics": full[best_i]},
            "wf": wf, "perm_p": None if p_val is None else round(p_val, 4), "dsr": dsr, "mc": mc,
            "grade": grade, "weight": weight,
            "current": {"params": s.grid[cur_i], "decision": decision, "fresh": fresh, "since_days": since,
                        "vote": _vote(s.family, decision),
                        "levels": {k: (None if v is None else round(v, 4)) for k, v in cur["levels"].items()}},
            "markers": _markers(cur["trades"], dates[-1] - pd.Timedelta(days=730)),
        })

    bh_full = bt.metrics(bh["returns"], rf_d, bh["held"], [], ppy)
    return {"strategies": results, "n_trials": len(trial_srs), "sr0_annual": round(sr0 * math.sqrt(ppy), 3),
            "buy_hold": {"full": bh_full, "oos": bh_oos_metrics}, "folds": len(folds),
            "bars": n, "start": str(dates[0].date()), "end": str(dates[-1].date())}


def strategy_score(v: dict) -> dict:
    """검증 등급으로 가중한 현재 신호 투표 -> 판단 엔진 모듈 점수."""
    used = [s for s in v["strategies"] if s["weight"] > 0 and s["family"] != "day"]
    if not used:
        return {"score": 0.0, "confidence": 0.0,
                "summary": "이 종목에서 통계적으로 검증을 통과한 기술 전략이 없습니다. 기술적 매매 신호는 판단에서 뺐습니다."}
    wsum = sum(s["weight"] for s in used)
    score = sum(s["weight"] * s["current"]["vote"] for s in used) / wsum
    conf = min(1.0, wsum / 2.0)
    longs = [s["label"] for s in used if s["current"]["vote"] > 0]
    shorts = [s["label"] for s in used if s["current"]["vote"] < 0]
    parts = []
    if longs:
        parts.append("보유 신호: " + ", ".join(longs))
    if shorts:
        parts.append("현금 신호: " + ", ".join(shorts))
    return {"score": round(score, 3), "confidence": round(conf, 3),
            "summary": f"검증 통과 {len(used)}개 전략의 가중 투표. " + " / ".join(parts)}
