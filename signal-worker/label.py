"""
M3 라벨링·정확도 측정 도구 (news_label)
============================================================
설계: docs/news-signal-design.md "M3 정확도 기준".

  python label.py label [--n 150]          기사x종목 쌍을 소스별로 고르게 뽑아 사람이 정답을 붙인다.
  python label.py eval  [--model-version]  news_label 과 news_signal 을 맞춰 질문별 정확도·ECE·게이트 판정.
  python label.py eval --labels x.csv       news_label 대신 CSV 라벨로 평가 (LLM 대리 라벨 등, news_label 에 섞지 않는다).
  ... eval --save                            결과를 news_eval 에 남긴다 (대시보드 'Laya 흐름' 패널).

VM 에서는 signal-worker 컨테이너 안에서 돌린다 (DB 접속 env 가 이미 있다):
  docker compose -f docker-compose.prod.yml exec signal-worker python label.py label

- 라벨링 화면에는 모델 예측을 보여 주지 않는다 (예측에 끌려가는 편향 방지).
- 라벨 값은 영어 키다. relevance: main|mention|unrelated, sentiment: positive|neutral|negative,
  event_type: questions.EVENT_TYPES 의 값. MARKET 쌍의 relevance 는 "시장 전체 기사인가" (main|unrelated).
- relevance 는 news_signal 에 p_relevant(=main 확률)만 있으므로 'main 인가' 이진 문제로 잰다.
  전략도 p_relevant >= 0.5 필터로만 쓰므로 같은 기준이다.
- 온도 보정: collected_at 순서로 앞 절반에서 T 를 맞추고 뒤 절반에서 ECE 를 잰다 (같은 표본으로 맞추고 재면 낙관적).
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from typing import Any

import psycopg
from psycopg.rows import dict_row

from questions import EVENT_TYPES
from store import dsn_from_env

RELEVANCE = {"a": "main", "m": "mention", "u": "unrelated"}
SENTIMENT = {"+": "positive", "0": "neutral", "-": "negative"}
EVENTS = {str(i + 1): name for i, name in enumerate(EVENT_TYPES.values())}
SENT_ORDER = ["positive", "neutral", "negative"]
EVENT_ORDER = list(EVENT_TYPES.values())

# 게이트 (설계서 M3 정확도 기준)
GATE = {"relevance": 0.80, "sentiment": 0.70, "event_type": 0.65}
SENT_OVER_BASELINE = 0.10
ECE_MAX = 0.10


def latest_model_version(conn) -> str | None:
    row = conn.execute("SELECT model_version FROM news_signal ORDER BY created_at DESC LIMIT 1").fetchone()
    return row["model_version"] if row else None


# ---------------------------------------------------------------- 지표 (순수 함수)

def accuracy(probs: list[list[float]], y: list[int]) -> float:
    return sum(max(range(len(p)), key=p.__getitem__) == t for p, t in zip(probs, y)) / len(y)


def majority_baseline(y: list[int]) -> float:
    return max(y.count(k) for k in set(y)) / len(y)


def ece(probs: list[list[float]], y: list[int], bins: int = 10) -> float:
    """top-1 신뢰도 기준 Expected Calibration Error (등간격 bin)."""
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for p, t in zip(probs, y):
        k = max(range(len(p)), key=p.__getitem__)
        buckets[min(int(p[k] * bins), bins - 1)].append((p[k], k == t))
    return sum(
        len(b) / len(y) * abs(sum(c for c, _ in b) / len(b) - sum(ok for _, ok in b) / len(b))
        for b in buckets if b
    )


def scale(p: list[float], t: float) -> list[float]:
    """확률에 온도 T 적용: p_i^(1/T) 정규화 (= log p 를 로짓으로 본 온도 보정)."""
    w = [math.exp(math.log(max(x, 1e-12)) / t) for x in p]
    s = sum(w)
    return [x / s for x in w]


def nll(probs: list[list[float]], y: list[int]) -> float:
    return -sum(math.log(max(p[t], 1e-12)) for p, t in zip(probs, y)) / len(y)


def fit_temperature(probs: list[list[float]], y: list[int]) -> float:
    # ponytail: 격자 탐색 (0.25~5.0, 0.05 간격). 표본이 수백 건이라 최적화기가 필요 없다.
    grid = [0.25 + 0.05 * i for i in range(96)]
    return min(grid, key=lambda t: nll([scale(p, t) for p in probs], y))


def report(name: str, probs: list[list[float]], y: list[int]) -> dict[str, Any]:
    """정확도·기준선·ECE(보정 전/후). 보정은 앞 절반에서 맞추고 뒤 절반에서 잰다."""
    half = len(y) // 2
    out: dict[str, Any] = {
        "field": name, "n": len(y),
        "acc": accuracy(probs, y), "baseline": majority_baseline(y),
        "ece_raw": ece(probs, y), "T": None, "ece_cal": None,
    }
    if half >= 10:
        t = fit_temperature(probs[:half], y[:half])
        out["T"] = t
        out["ece_cal"] = ece([scale(p, t) for p in probs[half:]], y[half:])
    return out


def gate(r: dict[str, Any]) -> bool:
    ok = r["acc"] >= GATE[r["field"]]
    if r["field"] == "sentiment":
        ok = ok and r["acc"] - r["baseline"] >= SENT_OVER_BASELINE
    if r["field"] != "event_type":  # event_type 은 미달이어도 진행 (그 피처만 뺀다)
        ok = ok and r["ece_cal"] is not None and r["ece_cal"] <= ECE_MAX
    return ok


# ---------------------------------------------------------------- 라벨링

def _ask(prompt: str, choices: dict[str, str]) -> str | None:
    """선택지 키 입력. 's' 건너뛰기 -> None, 'q' 종료 -> SystemExit."""
    while True:
        ans = input(prompt).strip().lower()
        if ans == "q":
            raise SystemExit(0)
        if ans == "s":
            return None
        if ans in choices:
            return choices[ans]


def sample_pairs(conn, model_version: str, n: int) -> list[dict[str, Any]]:
    """아직 라벨이 없는 (기사, 종목) 쌍을 소스별 라운드로빈으로 뽑는다."""
    return list(conn.execute(
        """
        SELECT article_id, symbol, source, title, body FROM (
            SELECT s.article_id, s.symbol, r.source, r.title, r.body,
                   row_number() OVER (PARTITION BY r.source ORDER BY random()) AS rn
            FROM news_signal s JOIN news_raw r USING (article_id)
            WHERE s.model_version = %s
              AND NOT EXISTS (SELECT 1 FROM news_label l
                              WHERE l.article_id = s.article_id AND l.symbol = s.symbol)
        ) x ORDER BY rn, source LIMIT %s
        """,
        (model_version, n),
    ))


def run_label(conn, model_version: str, n: int) -> None:
    pairs = sample_pairs(conn, model_version, n)
    print(f"[라벨링] model_version={model_version} 대상 {len(pairs)}쌍  (s=건너뛰기, q=종료)")
    ev_help = " ".join(f"{k}={v}" for k, v in EVENTS.items())
    for i, p in enumerate(pairs, 1):
        body = (p["body"] or "").strip().replace("\n", " ")
        print(f"\n--- {i}/{len(pairs)}  [{p['source']}]  종목={p['symbol']}")
        print(p["title"])
        if body:
            print(body[:600])
        rel_q = "시장 전체 기사? a=예 u=아니오: " if p["symbol"] == "MARKET" else "relevance a=주 대상 m=언급만 u=무관: "
        rel = _ask(rel_q, {"a": "main", "u": "unrelated"} if p["symbol"] == "MARKET" else RELEVANCE)
        if rel is None:
            continue
        sent = _ask("sentiment +=긍정 0=중립 -=부정: ", SENTIMENT)
        if sent is None:
            continue
        ev = _ask(f"event_type {ev_help}: ", EVENTS)
        if ev is None:
            continue
        with conn.transaction():
            for field, label in (("relevance", rel), ("sentiment", sent), ("event_type", ev)):
                conn.execute(
                    """
                    INSERT INTO news_label (article_id, symbol, field, label) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (article_id, symbol, field) DO UPDATE SET label = EXCLUDED.label, labeled_at = now()
                    """,
                    (p["article_id"], p["symbol"], field, label),
                )


# ---------------------------------------------------------------- 평가

def load_eval_rows(conn, model_version: str) -> list[dict[str, Any]]:
    return list(conn.execute(
        """
        SELECT s.collected_at, s.p_relevant, s.p_pos, s.p_neu, s.p_neg, s.p_event,
               max(l.label) FILTER (WHERE l.field = 'relevance')  AS relevance,
               max(l.label) FILTER (WHERE l.field = 'sentiment')  AS sentiment,
               max(l.label) FILTER (WHERE l.field = 'event_type') AS event_type
        FROM news_label l
        JOIN news_signal s ON s.article_id = l.article_id AND s.symbol = l.symbol AND s.model_version = %s
        GROUP BY s.article_id, s.symbol, s.collected_at, s.p_relevant, s.p_pos, s.p_neu, s.p_neg, s.p_event
        ORDER BY s.collected_at
        """,
        (model_version,),
    ))


def load_csv_rows(conn, model_version: str, path: str) -> list[dict[str, Any]]:
    """news_label 대신 CSV(article_id,symbol,relevance,sentiment,event_type) 의 라벨로 평가한다.

    사람이 아닌 라벨(예: LLM 대리 라벨)을 news_label 에 섞지 않고 따로 재 보기 위한 경로.
    """
    with open(path, newline="") as f:
        labels = {(r["article_id"], r["symbol"]): r for r in csv.DictReader(f)}
    sig = conn.execute(
        """
        SELECT article_id, symbol, collected_at, p_relevant, p_pos, p_neu, p_neg, p_event
        FROM news_signal WHERE model_version = %s AND article_id = ANY(%s)
        """,
        (model_version, list({a for a, _ in labels})),
    )
    rows = [{**s, **{k: labels[(s["article_id"], s["symbol"])][k] for k in GATE}}
            for s in sig if (s["article_id"], s["symbol"]) in labels]
    return sorted(rows, key=lambda r: r["collected_at"])


def build_sets(rows: list[dict[str, Any]]) -> dict[str, tuple[list[list[float]], list[int]]]:
    sets: dict[str, tuple[list[list[float]], list[int]]] = {f: ([], []) for f in GATE}
    for r in rows:
        if r["relevance"] and r["p_relevant"] is not None:
            sets["relevance"][0].append([r["p_relevant"], 1 - r["p_relevant"]])
            sets["relevance"][1].append(0 if r["relevance"] == "main" else 1)
        if r["sentiment"] in SENT_ORDER and r["p_pos"] is not None:
            sets["sentiment"][0].append([r["p_pos"], r["p_neu"], r["p_neg"]])
            sets["sentiment"][1].append(SENT_ORDER.index(r["sentiment"]))
        if r["event_type"] in EVENT_ORDER and r["p_event"]:
            sets["event_type"][0].append([float(r["p_event"].get(e, 0.0)) for e in EVENT_ORDER])
            sets["event_type"][1].append(EVENT_ORDER.index(r["event_type"]))
    return sets


def save_eval(conn, model_version: str, labels: str, reports: list[dict[str, Any]]) -> None:
    """측정 결과를 news_eval 에 남긴다 (대시보드 'Laya 흐름' 패널이 읽는다). 한 번의 실행 = 같은 run_at."""
    with conn.transaction():
        run_at = conn.execute("SELECT now() AS t").fetchone()["t"]
        for r in reports:
            conn.execute(
                """
                INSERT INTO news_eval (run_at, model_version, labels, field, n, acc, baseline,
                                       ece_raw, ece_cal, temperature, passed)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (run_at, model_version, labels, r["field"], r["n"], r["acc"], r["baseline"],
                 r["ece_raw"], r["ece_cal"], r["T"], r["passed"]),
            )


def run_eval(conn, model_version: str, labels_csv: str | None = None, save: bool = False) -> bool:
    rows = load_csv_rows(conn, model_version, labels_csv) if labels_csv else load_eval_rows(conn, model_version)
    sets = build_sets(rows)
    print(f"[평가] model_version={model_version} labels={labels_csv or 'news_label'}")
    passed = {}
    reports = []
    for field, (probs, y) in sets.items():
        if not y:
            print(f"  {field:<10} 라벨 없음")
            passed[field] = False
            continue
        r = report(field, probs, y)
        ci = 1.96 * math.sqrt(r["acc"] * (1 - r["acc"]) / r["n"])
        cal = "-" if r["ece_cal"] is None else f"{r['ece_cal']:.3f} (T={r['T']:.2f})"
        passed[field] = gate(r)
        reports.append({**r, "passed": passed[field]})
        print(f"  {field:<10} n={r['n']:<4} acc={r['acc']:.3f}±{ci:.3f} baseline={r['baseline']:.3f} "
              f"ece={r['ece_raw']:.3f} ece_cal={cal}  {'PASS' if passed[field] else 'FAIL'}")
    if save and reports:
        save_eval(conn, model_version, os.path.basename(labels_csv) if labels_csv else "news_label", reports)
        print("[저장] news_eval")
    go = passed["relevance"] and passed["sentiment"]
    print(f"[M4 진행] {'가능' if go else '불가'}"
          + ("" if passed["event_type"] else "  (event_type 미달: 전략에서 event 피처 제외)"))
    return go


def main() -> None:
    ap = argparse.ArgumentParser(description="news_label 라벨링 / Laya 정확도 측정")
    ap.add_argument("cmd", choices=["label", "eval"])
    ap.add_argument("--n", type=int, default=150, help="label: 이번에 볼 쌍 수")
    ap.add_argument("--model-version", help="기본: 가장 최근에 쓰인 model_version")
    ap.add_argument("--save", action="store_true", help="eval: 결과를 news_eval 에 남긴다 (대시보드에 표시)")
    ap.add_argument("--labels", help="eval: news_label 대신 이 CSV 의 라벨로 평가 (article_id,symbol,relevance,sentiment,event_type)")
    args = ap.parse_args()
    with psycopg.connect(dsn_from_env(), autocommit=True, row_factory=dict_row) as conn:
        mv = args.model_version or latest_model_version(conn)
        if mv is None:
            sys.exit("news_signal 이 비어 있습니다 (LAYA_ENABLED=true 로 먼저 분류)")
        if args.cmd == "label":
            run_label(conn, mv, args.n)
        else:
            sys.exit(0 if run_eval(conn, mv, args.labels, args.save) else 1)


if __name__ == "__main__":
    main()
