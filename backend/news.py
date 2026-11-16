"""뉴스 감성: 기사 제목을 NIM 으로 분류하고 시간 가중 평균을 낸다.

kaspflow 1세대는 Laya(로컬 모델, 상주 메모리 ~2.3GB)로 분류했다. 주식판은 NIM 으로 옮겨 VM 메모리를 비운다.
분류 항목은 open-jev 뉴스 이벤트 스키마를 줄인 것이다 (실적·가이던스·소송/규제·M&A·경영진 등).
한 번 분류한 기사는 기사 id 로 7일 캐시해 다시 돌릴 때 새 기사만 묻는다.

가중치 = 관련도 x 영향도(low 0.3 / medium 1 / high 2) x exp(-경과일/7)
  이미 일어난 주가 등락을 전하는 기사(price_move)는 원인이 아니라 결과라 0.3 배로 줄인다.
신뢰도 = 1 - exp(-가중치 합 / 2.5): 관련 기사가 많을수록 1 에 가까워진다.
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone

from pydantic import BaseModel

EVENTS = {"earnings": "실적", "guidance": "가이던스", "analyst": "애널리스트", "product": "제품·사업", "legal": "소송·규제",
          "mna": "M&A", "management": "경영진", "capital": "자본·배당", "macro": "거시·업황", "price_move": "주가 등락",
          "other": "기타"}
IMPACT_W = {"low": 0.3, "medium": 1.0, "high": 2.0}
BATCH = 20
LABEL_TTL = 7 * 24 * 3600
SYSTEM = ("너는 미국 주식 뉴스 분류기다. 기사 제목(과 요약)이 해당 기업의 주가에 주는 영향을 판단한다. "
          "제목에 적힌 사실만 근거로 삼고, 기사 본문이나 배경지식으로 추측하지 않는다.")


class Label(BaseModel):
    i: int
    relevance: float
    sentiment: float
    impact: str
    event: str
    title_ko: str


class Batch(BaseModel):
    items: list[Label]


def _prompt(symbol: str, name: str, sector: str | None, items: list[dict]) -> str:
    lines = [f"{k + 1}. [{it['published'][:10]}] {it['title']} — {it['source']}"
             + (f"\n   요약: {it['summary'][:200]}" if it.get("summary") else "") for k, it in enumerate(items)]
    return (f"종목: {symbol} ({name}), 섹터: {sector or '-'}\n기사 목록:\n" + "\n".join(lines) + "\n\n"
            "각 기사마다 다음을 매긴다. i 는 기사 번호다.\n"
            "- relevance: 이 기업이 기사의 주제인 정도 (0 무관, 0.5 여러 종목 중 하나로 언급, 1 이 기업이 중심)\n"
            "- sentiment: 이 기업 주가에 대한 방향 (-1 매우 부정 ~ 0 중립 ~ +1 매우 긍정)\n"
            "- impact: 주가 영향 크기 low/medium/high. 이미 일어난 등락을 전하는 기사, 시세·회사 소개 페이지, "
            "홍보성 글, '지금 사야 하나' 류 의견 글은 low\n"
            f"- event: {' | '.join(EVENTS)} 중 하나\n"
            "- title_ko: 제목을 자연스러운 한국어로 옮긴 것 (60자 이내)")


def _norm(lbl: Label) -> dict:
    return {"relevance": max(0.0, min(1.0, lbl.relevance)), "sentiment": max(-1.0, min(1.0, lbl.sentiment)),
            "impact": lbl.impact if lbl.impact in IMPACT_W else "low",
            "event": lbl.event if lbl.event in EVENTS else "other", "title_ko": lbl.title_ko[:120]}


async def label(items: list[dict], symbol: str, name: str, sector: str | None, llm, cache) -> tuple[list[dict], str | None]:
    """기사에 라벨을 붙인다. 반환: (라벨이 붙은 기사들, 오류 메시지)."""
    model = getattr(llm, "model", "llm")
    out, todo = [], []
    for it in items:
        hit = await asyncio.to_thread(cache.get, f"newslabel:v1:{model}:{it['id']}", LABEL_TTL) if cache else None
        (out.append({**it, **hit}) if hit else todo.append(it))
    error = None
    if todo and getattr(llm, "enabled", False):
        chunks = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
        results = await asyncio.gather(*[llm.generate("news", SYSTEM, _prompt(symbol, name, sector, c), Batch, 6000)
                                         for c in chunks], return_exceptions=True)
        for chunk, res in zip(chunks, results):
            if isinstance(res, Exception):
                error = str(res)[:200]
                continue
            by_i = {lb.i: lb for lb in res.items}
            for k, it in enumerate(chunk):
                lb = by_i.get(k + 1)
                if lb is None:
                    continue
                lab = _norm(lb)
                out.append({**it, **lab})
                if cache:
                    await asyncio.to_thread(cache.set, f"newslabel:v1:{model}:{it['id']}", lab)
    elif todo:
        error = "LLM 이 꺼져 있어 새 기사를 분류하지 못했습니다"
    labeled_ids = {x["id"] for x in out}
    out += [it for it in items if it["id"] not in labeled_ids]  # 분류 못 한 기사도 목록에는 남긴다
    out.sort(key=lambda x: x["published"], reverse=True)
    return out, error


def aggregate(items: list[dict], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = []
    for it in items:
        if "sentiment" not in it:
            continue
        age = max(0.0, (now - datetime.fromisoformat(it["published"])).total_seconds() / 86400)
        w = it["relevance"] * IMPACT_W[it["impact"]] * math.exp(-age / 7)
        if it["event"] == "price_move":
            w *= 0.3
        rows.append((it, w, age))
    total = sum(w for _, w, _ in rows)
    if not rows or total < 0.05:
        return {"available": False, "score": 0.0, "confidence": 0.0, "labeled": len(rows),
                "summary": "최근 3주간 이 종목과 관련된 분류 기사가 거의 없습니다."}
    score = sum(it["sentiment"] * w for it, w, _ in rows) / total

    def avg(sel):
        s = sum(w for _, w, _ in sel)
        return round(sum(it["sentiment"] * w for it, w, _ in sel) / s, 3) if s > 0 else None

    recent, older = [r for r in rows if r[2] <= 7], [r for r in rows if r[2] > 7]
    counts: dict[str, int] = {}
    for it, w, _ in rows:
        if it["relevance"] >= 0.5:
            counts[it["event"]] = counts.get(it["event"], 0) + 1
    drivers = sorted(rows, key=lambda r: abs(r[0]["sentiment"] * r[1]), reverse=True)[:5]
    pos = sum(1 for it, _, _ in rows if it["sentiment"] > 0.15 and it["relevance"] >= 0.5)
    neg = sum(1 for it, _, _ in rows if it["sentiment"] < -0.15 and it["relevance"] >= 0.5)
    conf = 1 - math.exp(-total / 2.5)
    tone = "긍정" if score > 0.15 else "부정" if score < -0.15 else "중립"
    return {"available": True, "score": round(score, 3), "confidence": round(conf, 3), "labeled": len(rows),
            "recent_7d": avg(recent), "older": avg(older), "positive": pos, "negative": neg,
            "events": {EVENTS[k]: v for k, v in sorted(counts.items(), key=lambda kv: -kv[1])},
            "drivers": [{"title": d[0].get("title_ko") or d[0]["title"], "sentiment": d[0]["sentiment"],
                         "event": EVENTS[d[0]["event"]], "date": d[0]["published"][:10]} for d in drivers],
            "summary": f"관련 기사 {len(rows)}건, 시간 가중 감성 {score:+.2f} ({tone}). 긍정 {pos}건 / 부정 {neg}건."}
