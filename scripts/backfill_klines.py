#!/usr/bin/env python3
"""
과거 1분봉 일회성 백필 (Binance klines -> ohlc_1m)
============================================================
[이 파일이 하는 일]
  대시보드는 Producer 가 켜진 시점부터의 체결만 쌓기 때문에 과거 데이터가 없다.
  Binance 공개 REST(klines)에서 과거 1분봉을 받아 ohlc_1m 에 **한 번** 채워 넣는다.

[Spark 가 만든 봉과 같은 의미인가?]
  Spark : @trade 스트림 체결을 1분 윈도우로 집계 (first/max/min/last 가격, sum 수량, count 건수)
  klines: 같은 체결을 Binance 가 1분 단위로 집계 (open/high/low/close, 기초자산 거래량, 체결 건수)
  -> open/high/low/close/volume/trade_count 가 같은 정의이므로 그대로 섞어 써도 된다.

[안전장치]
  - 기본은 DRY-RUN: Binance 에서 받아 와 "몇 행이 새로 들어갈지"만 출력하고 DB 는 SELECT 만 한다.
    실제로 쓰려면 --apply 를 붙인다.
  - INSERT ... ON CONFLICT DO NOTHING : 이미 있는 행(Spark 가 만든 것)은 절대 덮어쓰지 않는다.
    몇 번을 다시 돌려도 결과가 같다(멱등). 중간에 끊겨도 다시 돌리면 된다.
  - 심볼별로 한 트랜잭션: 실패하면 그 심볼은 하나도 안 들어간다.
  - 진행 중인 현재 분은 제외한다 (Spark 가 채우는 중).
  - Binance 호출은 심볼당 (일수 x 1.44) 회 정도라 레이트 리밋에 한참 못 미친다. 429/418 이면 멈춘다.

[실행 (서버) — 운영 backend 와 분리된 '일회용 컨테이너'로 돌린다]
  `exec` 로 운영 backend 컨테이너 안에서 돌리면 그 컨테이너의 메모리 한도(400MiB)를 같이 써서
  운영 API 가 OOM 위험에 노출된다. `run --rm` 은 같은 이미지/환경변수로 새 컨테이너를 띄워
  끝나면 지우므로 운영 서비스와 메모리가 분리된다.

  # 1) 먼저 DRY-RUN
  docker compose -f docker-compose.prod.yml run --rm -T --no-deps backend python - --days 30 < scripts/backfill_klines.py
  # 2) 확인 후 실제 적재
  docker compose -f docker-compose.prod.yml run --rm -T --no-deps backend python - --days 30 --apply < scripts/backfill_klines.py

[로컬 실행]
  POSTGRES_HOST=localhost POSTGRES_PORT=5432 python scripts/backfill_klines.py --days 7

[표준 라이브러리 + psycopg 만 사용한다. (backend 이미지에 이미 설치됨)]
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator

# 시장 데이터 전용 엔드포인트(인증 불필요). Producer 의 WS 도 같은 계열(data-stream.binance.vision)을 쓴다.
KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
MINUTE_MS = 60_000
PAGE_LIMIT = 1000  # klines 1회 최대 행 수
MAX_DAYS = 400     # 실수 방지용 상한 (아래 '행 수' 참고)
DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,XRPUSDT"

Row = tuple  # (symbol, window_start, open, high, low, close, volume, trade_count)


# ------------------------------------------------------------
# 순수 함수 (DB/네트워크 없이 테스트 가능)
# ------------------------------------------------------------
def floor_minute_ms(ts: datetime) -> int:
    """datetime -> 그 분의 시작(epoch ms)."""
    ms = int(ts.timestamp() * 1000)
    return ms - ms % MINUTE_MS


def parse_kline(symbol: str, k: list) -> Row:
    """Binance kline 배열 -> ohlc_1m 행.

    k = [open_time, open, high, low, close, volume, close_time, quote_volume, trades, ...]
    가격/수량은 문자열로 온다.
    """
    return (
        symbol,
        datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc),
        float(k[1]),
        float(k[2]),
        float(k[3]),
        float(k[4]),
        float(k[5]),
        int(k[8]),
    )


def http_get_json(url: str, retries: int = 5) -> list:
    """GET -> JSON. 5xx/네트워크 오류는 재시도, 429/418(레이트 리밋/차단)이면 즉시 중단한다."""
    delay = 1.0
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kaspflow-backfill/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                raise SystemExit(
                    f"Binance 가 요청을 제한했습니다 (HTTP {e.code}, Retry-After={e.headers.get('Retry-After')}). "
                    "잠시 후 다시 실행하세요. 진행분은 없습니다(심볼 단위 트랜잭션)."
                )
            if e.code < 500 or attempt == retries:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                raise
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("unreachable")


def fetch_pages(
    symbol: str,
    start_ms: int,
    end_ms: int,
    get_json: Callable[[str], list] = http_get_json,
    pause: float = 0.15,
) -> Iterator[list[Row]]:
    """[start_ms, end_ms) 구간의 1분봉을 '한 페이지(최대 1000행)씩' 시간 오름차순으로 생성한다.

    메모리를 페이지 하나 분량만 쓰도록 스트리밍한다. (1년치 = 심볼당 52만 행이라
    통째로 리스트에 담으면 컨테이너 메모리 한도를 넘는다.)
    startTime 이후의 '존재하는' 봉부터 돌려주므로(체결이 없는 분은 봉이 없다),
    빈 응답이 오면 더 이상 없다는 뜻이다.
    """
    cursor = start_ms
    while cursor < end_ms:
        q = urllib.parse.urlencode({
            "symbol": symbol, "interval": "1m",
            "startTime": cursor, "endTime": end_ms - 1, "limit": PAGE_LIMIT,
        })
        raw = get_json(f"{KLINES_URL}?{q}")
        if not raw:
            return
        page = [parse_kline(symbol, k) for k in raw if int(k[0]) < end_ms]  # 방어: endTime 이후 봉 제외
        if page:
            yield page
        if len(page) < len(raw):  # endTime 을 넘는 봉이 섞여 왔다면 끝
            return
        cursor = int(raw[-1][0]) + MINUTE_MS
        time.sleep(pause)


def fetch_range(symbol: str, start_ms: int, end_ms: int, get_json: Callable[[str], list] = http_get_json,
                pause: float = 0.15) -> Iterator[Row]:
    """fetch_pages 를 행 단위로 펼친 버전 (테스트/소량 용)."""
    for page in fetch_pages(symbol, start_ms, end_ms, get_json, pause):
        yield from page


# ------------------------------------------------------------
# DB
# ------------------------------------------------------------
INSERT_SQL = """
    INSERT INTO ohlc_1m (symbol, window_start, open, high, low, close, volume, trade_count)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (symbol, window_start) DO NOTHING
"""


def conninfo() -> str:
    g = os.getenv
    return (
        f"host={g('POSTGRES_HOST', 'postgres')} port={g('POSTGRES_PORT', '5432')} "
        f"dbname={g('POSTGRES_DB', 'crypto')} user={g('POSTGRES_USER', 'crypto')} "
        f"password={g('POSTGRES_PASSWORD', 'crypto')}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Binance 과거 1분봉 일회성 백필 (기본 DRY-RUN)")
    ap.add_argument("--days", type=int, default=30, help="며칠 전부터 채울지 (기본 30)")
    ap.add_argument("--symbols", default=os.getenv("SYMBOLS", DEFAULT_SYMBOLS), help="쉼표 구분 심볼")
    ap.add_argument("--apply", action="store_true", help="실제로 DB 에 적재 (없으면 DRY-RUN)")
    args = ap.parse_args()

    if not 1 <= args.days <= MAX_DAYS:
        print(f"--days 는 1~{MAX_DAYS} 범위여야 합니다.", file=sys.stderr)
        return 2
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("심볼이 비었습니다.", file=sys.stderr)
        return 2

    import psycopg  # 지연 import: 순수 함수 테스트에는 필요 없다

    end_ms = floor_minute_ms(datetime.now(timezone.utc))  # 진행 중인 현재 분은 제외
    start_ms = floor_minute_ms(datetime.now(timezone.utc) - timedelta(days=args.days))
    mode = "APPLY (DB 에 기록)" if args.apply else "DRY-RUN (DB 는 읽기만)"
    print(f"[{mode}] {args.days}일 "
          f"{datetime.fromtimestamp(start_ms/1000, timezone.utc):%Y-%m-%d %H:%M}Z ~ "
          f"{datetime.fromtimestamp(end_ms/1000, timezone.utc):%Y-%m-%d %H:%M}Z, symbols={symbols}")

    total_new = 0
    with psycopg.connect(conninfo()) as conn:
        for sym in symbols:
            fetched = overlap = inserted = 0
            first = last = None
            # 적재 모드: 심볼 전체를 한 트랜잭션으로 (실패하면 그 심볼은 하나도 안 들어간다).
            # 페이지별로 처리하므로 메모리는 한 페이지(<=1000행) 분량만 쓴다.
            tx = conn.transaction() if args.apply else contextlib.nullcontext()
            with tx:
                for n_page, page in enumerate(fetch_pages(sym, start_ms, end_ms), start=1):
                    have = {
                        r[0] for r in conn.execute(
                            "SELECT window_start FROM ohlc_1m WHERE symbol = %s AND window_start BETWEEN %s AND %s",
                            (sym, page[0][1], page[-1][1]),
                        ).fetchall()
                    }
                    new_rows = [r for r in page if r[1] not in have]
                    fetched += len(page)
                    overlap += len(page) - len(new_rows)
                    first = first or page[0][1]
                    last = page[-1][1]
                    if args.apply and new_rows:
                        with conn.cursor() as cur:
                            cur.executemany(INSERT_SQL, new_rows)
                            inserted += cur.rowcount
                    elif not args.apply:
                        inserted += len(new_rows)
                    if n_page % 50 == 0:
                        print(f"    ... {sym} {fetched}행 처리 (~{last:%Y-%m-%d %H:%M}Z)", flush=True)
            span = f"{first:%m-%d %H:%M} ~ {last:%m-%d %H:%M}" if first else "-"
            verb = "적재 완료" if args.apply else "새로 넣을 행"
            print(f"  {sym}: Binance {fetched:>7}행 ({span}) | 이미 DB 에 있음 {overlap:>7} | {verb} {inserted:>7}", flush=True)
            total_new += inserted

    print(f"합계: {'적재' if args.apply else '적재 예정'} {total_new}행"
          + ("" if args.apply else "  (실제로 쓰려면 --apply)"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
