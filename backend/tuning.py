"""판단 엔진 가중치 자동 조정 (백테스트 + 실제 판단 기록).

모듈 신호(점수 x 신뢰도)와 그 뒤 수익률의 순위 상관(IC, 스피어만)을 보고 가중치를 옮긴다.
  새 가중치 = (1 - λ) x 기본값 + λ x 근거,   근거 = 무리 몫 x max(IC, 0) / Σ max(IC, 0),   λ = n / (n + N0)
  - 표본이 적으면 λ 가 작아 기본값에 가깝다 (수축). 늘 기본값에서 다시 계산하므로 조정이 누적되지 않는다.
  - 한 모듈은 기본값의 2배를 넘지 않는다. IC 가 0 이하인 모듈은 근거 몫이 0 이라 기본값의 (1 - λ) 배로 줄어든다.
무리(조정 대상):
  - 백테스트 표본만 있을 때: 기술적 지표, 과거 유사 국면, 시장 국면 셋끼리만 나눈다 (셋의 합은 기본값 그대로).
    다른 모듈이 없는 표본으로는 셋 전체를 줄이는 게 나은지 알 수 없기 때문이다.
  - 실제 판단 기록이 기간마다 MIN_LIVE 개(독립 표본 기준) 넘게 쌓이면: 모든 모듈을 함께 조정한다.
채택: 날짜 앞 70% 로 고른 가중치가 뒤 30% 에서 기본값보다 결합 점수 IC 가 MARGIN 이상 높을 때만, 전체 표본으로 다시 고른
가중치를 쓴다. 아니면 그 기간은 기본값을 쓴다. 결과는 데이터만으로 정해진다 (전에 무엇을 채택했는지와 무관).
"""
from __future__ import annotations

from datetime import date

import pandas as pd

import verdict as vd

TESTED = ("technical", "analogs", "regime")   # 과거 시점 값이 있어 백테스트할 수 있는 모듈
N0 = 500          # 독립 표본이 이만큼이면 근거와 기본값을 반반 믿는다
MIN_N = 30        # 모듈별 최소 독립 표본
MIN_LIVE = 60     # 모든 모듈을 조정하기 위한 실제 기록의 최소 독립 표본 (기간별)
MARGIN = 0.01     # 채택에 필요한 표본 밖 IC 개선 폭
SPLIT = 0.7


def spearman(a, b) -> float | None:
    if len(a) < MIN_N:
        return None
    r = pd.Series(a).rank().corr(pd.Series(b).rank())
    return None if pd.isna(r) else float(r)


def n_eff(samples: list[dict], h: str) -> int:
    """겹치는 수익률 창을 하나로 센 독립 표본 수: 종목마다 판단 기간 길이의 구간에 하나."""
    days = vd.HORIZON_DAYS[h]
    return len({(s["sym"], date.fromisoformat(s["date"]).toordinal() // days) for s in samples})


def score(x: dict, w: dict) -> float | None:
    """판단 엔진과 같은 결합: Σ(w x 점수 x 신뢰도) / Σw (있는 모듈만)."""
    used = [m for m in x if w.get(m, 0) > 0]
    return sum(w[m] * x[m] for m in used) / sum(w[m] for m in used) if used else None


def combined_ic(samples: list[dict], w: dict) -> float | None:
    pairs = [(s, score(s["x"], w)) for s in samples]
    pairs = [(s["ret"], v) for s, v in pairs if v is not None]
    return spearman([v for _, v in pairs], [r for r, _ in pairs])


def module_ic(samples: list[dict], m: str, h: str) -> tuple[float | None, int]:
    xs = [s for s in samples if m in s["x"]]
    return spearman([s["x"][m] for s in xs], [s["ret"] for s in xs]), n_eff(xs, h)


def propose(samples: list[dict], prior: dict, group: tuple | list, h: str, keep_total: bool) -> tuple[dict, dict]:
    ics = {m: module_ic(samples, m, h) for m in group}
    ok = {m: (ic, n) for m, (ic, n) in ics.items() if ic is not None and n >= MIN_N}
    mass = sum(prior[m] for m in group)
    pos = {m: max(ic, 0.0) for m, (ic, _) in ok.items()}
    tot = sum(pos.values())
    new = dict(prior)
    for m, (_, n) in ok.items():
        lam = n / (n + N0)
        evidence = mass * pos[m] / tot if tot > 0 else 0.0
        new[m] = min((1 - lam) * prior[m] + lam * evidence, 2 * prior[m])
    if keep_total and ok:
        k = mass / sum(new[m] for m in group)
        new.update({m: new[m] * k for m in group})
    return {m: round(v, 3) for m, v in new.items()}, {m: {"ic": None if ic is None else round(ic, 3), "n": n} for m, (ic, n) in ics.items()}


def tune_horizon(samples: list[dict], h: str, prior: dict) -> tuple[dict, dict]:
    """한 기간: (쓸 가중치, 보고서)."""
    s = sorted((x for x in samples if x["h"] == h), key=lambda x: x["date"])
    live = [x for x in s if x.get("src") == "live"]
    n_live = n_eff(live, h)
    everything = n_live >= MIN_LIVE
    group, mode = (tuple(prior), "all") if everything else (TESTED, "backtest")
    report = {"mode": mode, "n": len(s), "n_eff": n_eff(s, h), "n_live": n_live}
    if len(s) < 2 * MIN_N:
        return prior, {**report, "adopted": False, "reason": "표본이 부족해 기본값을 씀"}
    cut = s[int(len(s) * SPLIT)]["date"]
    train, test = [x for x in s if x["date"] < cut], [x for x in s if x["date"] >= cut]
    w_train, _ = propose(train, prior, group, h, not everything)
    ic_prior, ic_new = combined_ic(test, prior), combined_ic(test, w_train)
    final, ics = propose(s, prior, group, h, not everything)
    adopted = ic_prior is not None and ic_new is not None and ic_new >= ic_prior + MARGIN and final != prior
    report.update({"ic": ics, "oos": {"cut": cut, "prior": None if ic_prior is None else round(ic_prior, 3),
                                       "proposed": None if ic_new is None else round(ic_new, 3)}, "adopted": adopted,
                   "reason": "표본 밖 검증을 통과해 조정함" if adopted else "표본 밖에서 기본값보다 낫지 않아 기본값을 씀"})
    return (final if adopted else prior), report


def tune(samples: list[dict], prior: dict | None = None) -> tuple[dict, dict]:
    """모든 기간: ({기간: {모듈: w}}, {기간: 보고서})."""
    prior = prior or vd.WEIGHTS
    out, reports = {}, {}
    for h in prior:
        out[h], reports[h] = tune_horizon(samples, h, prior[h])
    return out, reports


def live_samples(rows: list[dict], price_on) -> list[dict]:
    """실제 판단 기록 -> 표본. rows: {symbol, created_at(datetime), price, modules(카드 목록), analogs(기간별)}.
    price_on(symbol, datetime) -> 그날(이후 첫 거래일) 종가 또는 None. 같은 종목·같은 날 여러 번 실행했으면 마지막 것만 쓴다."""
    out: dict[tuple, dict] = {}
    for r in sorted(rows, key=lambda r: r["created_at"]):
        base = {c["key"]: c["score"] * c["confidence"] for c in (r.get("modules") or []) if c.get("available") and c["key"] != "analogs"}
        for h, days in vd.HORIZON_DAYS.items():
            due = r["created_at"] + pd.Timedelta(days=days)
            end = price_on(r["symbol"], due)
            if end is None or not r.get("price"):
                continue
            a = (r.get("analogs") or {}).get(h) or {}
            x = {**base, **({"analogs": a["score"] * a["confidence"]} if a.get("available") else {})}
            d = r["created_at"].date().isoformat()
            out[(r["symbol"], d, h)] = {"h": h, "date": d, "sym": r["symbol"], "x": {k: round(v, 4) for k, v in x.items()},
                                        "ret": float(end / r["price"] - 1), "src": "live"}
    return list(out.values())


if __name__ == "__main__":   # 로컬에서 백테스트 표본만으로 한 번 돌려 보기
    import json

    import engine_backtest as eb
    rows, _ = eb.collect(log=lambda *_: None)
    w, rep = tune(rows)
    print(json.dumps({"weights": w, "report": rep}, ensure_ascii=False, indent=1))
    print("prior", json.dumps(vd.WEIGHTS))
