"""
Laya 질문 스키마 (schema_version = 2)
============================================================
모든 질문을 choice 유형으로 묻는다. 예·아니오(noul)와 등급(score)은 모델 카드가 약점으로 밝혀 쓰지 않는다.
선택지 키는 A/B/C..., 설명은 영어, 설명은 짧게 쓴다 (선택지들이 질문당 고정 토큰 예산을 나눠 쓰므로).

!! 질문 문구나 선택지를 바꾸면 SCHEMA_VERSION 을 올려야 한다. 이전 버전 결과는 지우지 않는다. !!
설계: docs/news-signal-design.md 4.2절
"""
from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 2
# 버전 기록
#   1: relevance = "Is this article mainly about {coin}?" (mainly / mentions / not about it)
#   2: relevance 를 "주된 대상은 무엇인가" 다지선다로 변경 (2026-10-18). 대리 라벨 146쌍에서
#      이진 정확도 0.575 -> 0.705, AUC 0.64 -> 0.78. sentiment·event_type·scope 는 그대로.

# relevance 선택지 키 -> 의미. 'A'(= 그 종목) 가 '주 대상' (p_relevant)
RELEVANCE_MAIN = "A"
# sentiment 선택지 키 -> 컬럼
SENTIMENT_KEYS = {"A": "p_pos", "B": "p_neu", "C": "p_neg"}
# event_type 선택지 키 -> 이벤트 이름 (news_signal.event_type / p_event 의 키)
EVENT_TYPES = {
    "A": "listing_delisting",
    "B": "regulation_legal",
    "C": "hack_security",
    "D": "institutional_etf_flow",
    "E": "partnership_tech",
    "F": "macro",
    "G": "other",
}
# scope 선택지: 'A' 가 시장 전체 기사
SCOPE_MARKET = "A"

_EVENT_CRITERIA = {
    "A": "listing or delisting on an exchange",
    "B": "regulation, lawsuit or legal ruling",
    "C": "hack, exploit or security incident",
    "D": "institutional adoption or ETF flows",
    "E": "partnership or technology upgrade",
    "F": "macroeconomy, rates or inflation",
    "G": "none of the above",
}


def _relevance(name: str) -> dict[str, Any]:
    return {
        "type": "choice",
        "instructions": "What is the main subject of this article?",
        "criteria": {
            "A": name,
            "B": "another coin, token or company",
            "C": "the crypto market overall",
            "D": "regulation, politics or the economy",
        },
    }


def _sentiment(name: str) -> dict[str, Any]:
    return {
        "type": "choice",
        "instructions": f"What is the sentiment of this article toward {name}?",
        "criteria": {"A": "positive", "B": "neutral", "C": "negative"},
    }


def _event_type() -> dict[str, Any]:
    return {
        "type": "choice",
        "instructions": "What kind of event does this article report?",
        "criteria": dict(_EVENT_CRITERIA),
    }


def _scope() -> dict[str, Any]:
    return {
        "type": "choice",
        "instructions": "Is this article about the crypto market as a whole?",
        "criteria": {"A": "the whole crypto market", "B": "something else"},
    }


def coin_questions(name: str) -> dict[str, dict[str, Any]]:
    """종목 기사용: relevance + sentiment + event_type (한 번의 호출로 묻는다)."""
    return {"relevance": _relevance(name), "sentiment": _sentiment(name), "event_type": _event_type()}


def market_questions(name: str) -> dict[str, dict[str, Any]]:
    """종목 매칭이 없는 기사용: scope + sentiment + event_type. scope=A 면 MARKET 신호로 저장한다."""
    return {"scope": _scope(), "sentiment": _sentiment(name), "event_type": _event_type()}
