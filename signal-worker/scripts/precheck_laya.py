"""
M0: Laya 사전 검증 (맥/로컬에서 실행)
============================================================
영어 코인 기사 제목 N건(기본 20)으로 설치·메모리·처리 시간·분류 결과를 확인한다.
  - 기사: 실제 RSS 피드(공식)에서 받아 온다 (기본 6개 소스에서 고르게)
  - 모델: signal-worker 가 쓰는 LayaClassifier 그대로 (CPU 고정, 스레드 2)
  - 출력: 로드 시간, 호출당 시간(중앙값/최대), 현재/최대 메모리(RSS), 기사별 분류 표

!! VM(arm64 2 OCPU)은 보통 맥보다 느리다. 여기서 잰 처리 시간은 상한이 아니라 하한으로 읽는다. !!

실행 (laya 가 설치된 가상환경에서):
  pip install laya feedparser httpx pyyaml
  cd signal-worker && python scripts/precheck_laya.py --n 20 [--subfolder multilingual]
"""
from __future__ import annotations

import argparse
import os
import resource
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import feedparser  # noqa: E402
import httpx  # noqa: E402

from classifier import LayaClassifier, build_text  # noqa: E402
from matching import load_book, match_symbols  # noqa: E402

FEEDS = [
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("theblock", "https://www.theblock.co/rss.xml"),
    ("decrypt", "https://decrypt.co/feed"),
    ("bitcoinmagazine", "https://bitcoinmagazine.com/feed"),
]


def rss_mb() -> float:
    """현재 프로세스의 상주 메모리(MB). (ru_maxrss 는 최대값이라 따로 현재값을 ps 로 읽는다)"""
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True).stdout
    return int(out.strip() or 0) / 1024


def peak_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024  # macOS 는 byte, Linux 는 KB


def fetch_articles(n: int) -> list[dict]:
    per_source = max(1, -(-n // len(FEEDS)))
    rows: list[dict] = []
    with httpx.Client(headers={"User-Agent": "kaspflow-precheck/1.0"}, timeout=20, follow_redirects=True) as c:
        for source, url in FEEDS:
            try:
                entries = feedparser.parse(c.get(url).content).entries[:per_source]
            except Exception as e:  # 한 소스가 실패해도 계속
                print(f"[건너뜀] {source}: {e!r}")
                continue
            for e in entries:
                rows.append({"source": source, "title": (e.get("title") or "").strip(),
                             "body": (e.get("summary") or "").strip()})
    return rows[:n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--subfolder", default=None, help="예: multilingual (기본은 영어 체크포인트)")
    args = ap.parse_args()

    print(f"[1/3] 기사 {args.n}건 수집 ...")
    articles = fetch_articles(args.n)
    print(f"      {len(articles)}건 (소스: {sorted({a['source'] for a in articles})})")

    print("[2/3] 모델 로드 (첫 실행은 가중치 다운로드 포함) ...")
    t0 = time.time()
    clf = LayaClassifier(subfolder=args.subfolder)
    load_s = time.time() - t0
    print(f"      model_version={clf.model_version} load={load_s:.1f}s rss={rss_mb():.0f}MB peak={peak_mb():.0f}MB")

    print("[3/3] 분류 ...")
    book = load_book()
    times: list[float] = []
    print(f"{'src':<14}{'sym':<9}{'rel':>5}{'pos':>6}{'neu':>6}{'neg':>6}  {'event':<24} title")
    for a in articles:
        text = build_text(a["title"], a["body"])
        syms = match_symbols(text, book)
        targets = syms or ["(market?)"]
        for sym in targets:
            t = time.time()
            if sym == "(market?)":
                r = clf.classify_market(text, book.market_name)
            else:
                r = clf.classify_coin(text, book.name_of(sym))
            times.append(time.time() - t)
            if r is None:
                print(f"{a['source']:<14}{'-':<9}{'':>5}{'':>6}{'':>6}{'':>6}  {'(not market-wide)':<24} {a['title'][:70]}")
                continue
            label = "MARKET" if sym == "(market?)" else sym
            print(f"{a['source']:<14}{label:<9}{r.p_relevant:>5.2f}{r.p_pos:>6.2f}{r.p_neu:>6.2f}{r.p_neg:>6.2f}  "
                  f"{r.event_type:<24} {a['title'][:70]}")

    first, rest = times[0], times[1:] or times
    print("\n--- 요약 ---")
    print(f"호출 {len(times)}회  첫 호출={first:.2f}s  중앙값={statistics.median(rest):.2f}s  최대={max(times):.2f}s")
    print(f"로드={load_s:.1f}s  RSS(현재)={rss_mb():.0f}MB  RSS(최대)={peak_mb():.0f}MB")
    print("※ VM 은 이보다 느리다고 보고, 처리 시간은 하한으로 읽을 것.")


if __name__ == "__main__":
    main()
