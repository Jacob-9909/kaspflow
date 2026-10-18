"""
Laya 분류기 (선택 의존성)
============================================================
signal-worker 는 LAYA_ENABLED=true 일 때만 이 모듈의 LayaClassifier 를 만든다.
M2(저장 전용)에서는 torch/laya 가 이미지에 없어도 되도록, laya 는 LayaClassifier 안에서만 import 한다.

Classifier 프로토콜(아래 두 메서드)만 지키면 main.process_message 가 그대로 쓴다 -> 테스트에서 가짜로 대체.
  classify_coin(text, name)   -> Classification
  classify_market(text, name) -> Classification | None   (시장 전체 기사가 아니면 None)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from questions import (
    EVENT_TYPES,
    RELEVANCE_MAIN,
    SCHEMA_VERSION,
    SCOPE_MARKET,
    SENTIMENT_KEYS,
    coin_questions,
    market_questions,
)

# 제목+요약만 쓰므로 512 토큰 안에 들어간다. 토크나이저 전에 문자 수로 한 번 더 자른다.
MAX_TEXT_CHARS = 1800
# 시장 전체 기사로 인정하는 scope 확률 하한.
# M0(맥, 영어 헤드라인 20건)에서 scope 질문이 시장 전체가 아닌 기사(예: 개별 기업·토큰화 주식 기사)에도
# 0.8 안팎을 줘서 과발동했다. 라벨로 정확도를 재기 전까지는 보수적으로 높게 둔다 (env 로 조정).
MARKET_MIN_P = float(os.getenv("LAYA_MARKET_MIN_P", "0.9"))


@dataclass(frozen=True)
class Classification:
    p_relevant: float  # relevance '주된 대상 = 이 종목' 확률 (MARKET 은 scope 'whole market' 확률)
    p_pos: float
    p_neu: float
    p_neg: float
    event_type: str  # top-1 이벤트 이름
    p_event: dict[str, float] = field(default_factory=dict)  # 이벤트 이름 -> 확률 (전체 분포)


class Classifier(Protocol):
    model_version: str

    def classify_coin(self, text: str, name: str) -> Classification: ...

    def classify_market(self, text: str, name: str) -> Classification | None: ...


def build_text(title: str, body: str | None) -> str:
    """Laya 에 넘기는 입력: 제목 + 요약."""
    text = title.strip()
    if body and body.strip():
        text = f"{text}\n\n{body.strip()}"
    return text[:MAX_TEXT_CHARS]


def _probs(answer: dict[str, Any], keys: set[str], what: str) -> dict[str, float]:
    probs = answer.get("probabilities")
    if not isinstance(probs, dict) or not keys <= set(probs):
        raise ValueError(f"Laya 응답에 {what} 확률이 없습니다: {answer!r}")
    return {k: float(probs[k]) for k in keys}


def parse_answers(answers: dict[str, Any], first: str) -> Classification:
    """Laya 의 answers 딕셔너리 -> Classification. first 는 'relevance' 또는 'scope'."""
    if first == "relevance":
        p_first = _probs(answers["relevance"], {RELEVANCE_MAIN}, "relevance")[RELEVANCE_MAIN]
    else:
        p_first = _probs(answers["scope"], {SCOPE_MARKET}, "scope")[SCOPE_MARKET]
    sent = _probs(answers["sentiment"], set(SENTIMENT_KEYS), "sentiment")
    ev_raw = _probs(answers["event_type"], set(EVENT_TYPES), "event_type")
    p_event = {EVENT_TYPES[k]: v for k, v in ev_raw.items()}
    top = max(p_event, key=p_event.get)
    return Classification(
        p_relevant=p_first,
        p_pos=sent["A"], p_neu=sent["B"], p_neg=sent["C"],
        event_type=top, p_event=p_event,
    )


class LayaClassifier:
    """영어 체크포인트(기본) Laya 로 종목/시장 기사를 분류한다."""

    def __init__(self, checkpoint: str | None = None, subfolder: str | None = None, threads: int | None = None) -> None:
        import importlib.metadata

        import laya  # 선택 의존성: LAYA_ENABLED=true 일 때만 여기까지 온다
        import torch

        checkpoint = checkpoint or os.getenv("LAYA_CHECKPOINT", "convaiinnovations/laya")
        subfolder = subfolder if subfolder is not None else (os.getenv("LAYA_SUBFOLDER") or None)
        torch.set_num_threads(threads or int(os.getenv("LAYA_THREADS", "2")))  # VM 2 OCPU
        # CPU 고정: VM 에는 GPU 가 없고, 맥(MPS)과 결과 재현성을 맞추기 위해서다.
        self._agent = laya.load(checkpoint, subfolder=subfolder, device="cpu")
        tag = "en" if subfolder is None else subfolder
        self.model_version = f"laya-{tag}@{importlib.metadata.version('laya')}/schema{SCHEMA_VERSION}"

    def _predict(self, text: str, questions: dict[str, Any]) -> dict[str, Any]:
        return self._agent.predict(text, questions)["answers"]

    def classify_coin(self, text: str, name: str) -> Classification:
        return parse_answers(self._predict(text, coin_questions(name)), "relevance")

    def classify_market(self, text: str, name: str) -> Classification | None:
        result = parse_answers(self._predict(text, market_questions(name)), "scope")
        return result if result.p_relevant >= MARKET_MIN_P else None
