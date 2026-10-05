"""
klines 백필 스크립트의 순수 함수 테스트 (DB/네트워크 불필요)

실행:  cd backend && python -m unittest discover -s tests -v
"""
import importlib.util
import os
import unittest
from datetime import datetime, timezone

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "backfill_klines.py")
spec = importlib.util.spec_from_file_location("backfill_klines", SCRIPT)
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)

M = bf.MINUTE_MS


def kline(open_ms, o="1", h="2", lo="0.5", c="1.5", v="10", trades=7):
    # Binance kline 배열 형식 (인덱스 0=open_time, 1~4=OHLC, 5=volume, 8=trades)
    return [open_ms, o, h, lo, c, v, open_ms + M - 1, "0", trades, "0", "0", "0"]


class TestParse(unittest.TestCase):
    def test_parse_kline_maps_fields(self):
        row = bf.parse_kline("BTCUSDT", kline(1_700_000_040_000, "100.5", "101", "99.5", "100", "3.25", 42))
        self.assertEqual(row[0], "BTCUSDT")
        self.assertEqual(row[1], datetime(2023, 11, 14, 22, 14, tzinfo=timezone.utc))
        self.assertEqual(row[2:], (100.5, 101.0, 99.5, 100.0, 3.25, 42))

    def test_floor_minute(self):
        t = datetime(2026, 1, 1, 0, 0, 59, 999000, tzinfo=timezone.utc)
        self.assertEqual(bf.floor_minute_ms(t), int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000))


class TestFetchRange(unittest.TestCase):
    def fake(self, minutes):
        """minutes: 존재하는 분(open_ms) 목록. Binance 처럼 startTime 이후 limit 개를 돌려준다."""
        calls = []

        def get_json(url):
            from urllib.parse import parse_qs, urlparse
            q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
            calls.append(q)
            start, end, limit = int(q["startTime"]), int(q["endTime"]), int(q["limit"])
            return [kline(m) for m in minutes if start <= m <= end][:limit]

        return get_json, calls

    def test_paginates_and_stops(self):
        start = 10 * M
        minutes = [start + i * M for i in range(2500)]  # 1000 단위 페이지 3번
        get_json, calls = self.fake(minutes)
        rows = list(bf.fetch_range("X", start, start + 2500 * M, get_json, pause=0))
        self.assertEqual(len(rows), 2500)
        self.assertEqual(len(calls), 3)
        times = [r[1] for r in rows]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(set(times)), 2500, "중복 봉이 없어야 함")

    def test_skips_minutes_without_trades(self):
        start = 0
        minutes = [0, M, 5 * M, 6 * M]  # 2~4분은 체결 없음
        get_json, _ = self.fake(minutes)
        rows = list(bf.fetch_range("X", start, 10 * M, get_json, pause=0))
        self.assertEqual(len(rows), 4)

    def test_never_returns_rows_at_or_after_end(self):
        get_json, _ = self.fake([0, M, 2 * M, 3 * M])
        # 서버가 endTime 을 무시하고 더 돌려줘도 end 이후는 버린다
        rows = list(bf.fetch_range("X", 0, 2 * M, lambda url: [kline(0), kline(M), kline(2 * M)], pause=0))
        self.assertEqual([int(r[1].timestamp() * 1000) for r in rows], [0, M])

    def test_empty_range(self):
        get_json, calls = self.fake([])
        self.assertEqual(list(bf.fetch_range("X", 0, 5 * M, get_json, pause=0)), [])
        self.assertEqual(len(calls), 1)

    def test_fetch_pages_streams_bounded_pages(self):
        # 메모리 사고 방지: 한 번에 최대 1000행 페이지로만 돌려준다 (전체를 리스트로 모으지 않음)
        minutes = [i * M for i in range(2500)]
        get_json, _ = self.fake(minutes)
        sizes = [len(p) for p in bf.fetch_pages("X", 0, 2500 * M, get_json, pause=0)]
        self.assertEqual(sizes, [1000, 1000, 500])


if __name__ == "__main__":
    unittest.main()
