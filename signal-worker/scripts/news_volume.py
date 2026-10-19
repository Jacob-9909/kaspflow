"""
M2 기사량 실측 (설계서 8절): 기간 안 수집 기사·신선한 기사(발행 6시간 안 수집) 수, 소스별·종목별·시간대별, 신선한 기사 간격.

컨테이너 이미지에는 들어 있지 않다. VM 에서 stdin 으로 넘겨 돌린다:
  ssh oracle_vm 'cd ~/kaspflow && docker compose -f docker-compose.prod.yml exec -T signal-worker python - 2026-10-18T15:00Z' \
    < signal-worker/scripts/news_volume.py
인자: 시작 시각(UTC, 기본 24시간 전), 끝 시각(기본 지금).
"""
import sys
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from store import dsn_from_env

now = datetime.now(timezone.utc)
start = datetime.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else now - timedelta(hours=24)
end = datetime.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else now
hours = (end - start).total_seconds() / 3600
FRESH = "r.collected_at - r.published_at <= interval '6 hours'"

with psycopg.connect(dsn_from_env(), row_factory=dict_row) as c:
    def q(sql):
        return c.execute(sql, {"s": start, "e": end}).fetchall()

    W = "r.collected_at >= %(s)s AND r.collected_at < %(e)s"
    t = q(f"SELECT count(*) n, count(*) FILTER (WHERE {FRESH}) fresh FROM news_raw r WHERE {W}")[0]
    print(f"[기간] {start:%Y-%m-%d %H:%M}Z ~ {end:%Y-%m-%d %H:%M}Z ({hours:.1f}h)")
    print(f"[전체] 수집 {t['n']}건, 신선 {t['fresh']}건 -> 하루 환산 {t['fresh'] * 24 / max(hours, 1e-9):.1f}건")
    print("[소스] " + ", ".join(f"{r['source']} {r['n']}/{r['fresh']}" for r in q(
        f"SELECT source, count(*) n, count(*) FILTER (WHERE {FRESH}) fresh FROM news_raw r WHERE {W} GROUP BY 1 ORDER BY 2 DESC")))
    print("[종목 신선 신호] " + ", ".join(f"{r['symbol']} {r['fresh']}/{r['n']}" for r in q(
        f"""SELECT s.symbol, count(DISTINCT s.article_id) n, count(DISTINCT s.article_id) FILTER (WHERE {FRESH}) fresh
            FROM news_signal s JOIN news_raw r USING (article_id) WHERE {W} GROUP BY 1 ORDER BY 1""")))
    print("[시간대 UTC 신선] " + " ".join(f"{int(r['h']):02d}:{r['n']}" for r in q(
        f"SELECT extract(hour FROM r.collected_at) h, count(*) n FROM news_raw r WHERE {W} AND {FRESH} GROUP BY 1 ORDER BY 1")))
    g = q(f"""SELECT percentile_cont(array[0.5, 0.9]) WITHIN GROUP (ORDER BY g) p, max(g) mx FROM (
              SELECT extract(epoch FROM r.collected_at - lag(r.collected_at) OVER (ORDER BY r.collected_at)) / 60 g
              FROM news_raw r WHERE {W} AND {FRESH}) t WHERE g IS NOT NULL""")[0]
    if g["p"]:
        print(f"[신선 기사 간격(분)] p50 {g['p'][0]:.0f}, p90 {g['p'][1]:.0f}, 최대 {float(g['mx']):.0f}")
