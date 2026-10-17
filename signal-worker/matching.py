"""
종목 매칭 (순수 함수 — Kafka/DB/모델 없이 테스트 가능)
============================================================
기사 제목+본문에서 symbols.yaml 의 별칭을 찾아 어느 종목 기사인지 정한다.
매칭이 하나도 없으면 호출자가 Laya 에게 '시장 전체 기사인지' 묻는다 (main.process_message).

설계: docs/news-signal-design.md 4.2절 (단어 경계, 티커는 대소문자 구분)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SYMBOLS_PATH = Path(__file__).with_name("symbols.yaml")


@dataclass(frozen=True)
class Symbol:
    symbol: str
    name: str  # Laya 질문에 쓰는 이름
    pattern: re.Pattern
    excludes: tuple[re.Pattern, ...]


@dataclass(frozen=True)
class SymbolBook:
    symbols: tuple[Symbol, ...]
    market_symbol: str
    market_name: str

    def name_of(self, symbol: str) -> str:
        if symbol == self.market_symbol:
            return self.market_name
        for s in self.symbols:
            if s.symbol == symbol:
                return s.name
        raise KeyError(symbol)


def _is_ticker(alias: str) -> bool:
    """전부 대문자이고 5자 이하면 티커로 보고 대소문자를 구분한다."""
    return alias.isupper() and len(alias) <= 5


def _word(alias: str) -> str:
    # 앞뒤가 영숫자가 아닐 때만 인정: BTC-USD, "BTC," 는 걸리고 BTCUSD, abtc 는 안 걸린다.
    return rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])"


def build_book(config: dict[str, Any]) -> SymbolBook:
    """symbols.yaml 내용 -> 컴파일된 매처."""
    symbols: list[Symbol] = []
    for sym, spec in (config.get("symbols") or {}).items():
        aliases = spec.get("aliases") or []
        if not aliases:
            raise ValueError(f"{sym}: aliases 가 비어 있습니다")
        # 티커는 대소문자 구분, 이름은 구분 없음 -> 별칭마다 다른 플래그가 필요해서 인라인 플래그 (?i:...) 를 쓴다.
        parts = []
        for a in aliases:
            w = _word(a)
            parts.append(w if _is_ticker(a) else f"(?i:{w})")
        pattern = re.compile("|".join(parts))
        excludes = tuple(re.compile(_word(x), re.I) for x in (spec.get("exclude") or []))
        symbols.append(Symbol(sym, spec.get("name") or sym, pattern, excludes))
    market = config.get("market") or {}
    return SymbolBook(tuple(symbols), market.get("symbol", "MARKET"), market.get("name", "the overall crypto market"))


def load_book(path: str | Path | None = None) -> SymbolBook:
    with open(path or DEFAULT_SYMBOLS_PATH, encoding="utf-8") as f:
        return build_book(yaml.safe_load(f))


def match_symbols(text: str, book: SymbolBook) -> list[str]:
    """텍스트에 등장하는 종목 심볼들 (symbols.yaml 의 순서). 없으면 빈 리스트."""
    found = []
    for s in book.symbols:
        cleaned = text
        for ex in s.excludes:
            cleaned = ex.sub(" ", cleaned)
        if s.pattern.search(cleaned):
            found.append(s.symbol)
    return found
