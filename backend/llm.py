"""NVIDIA NIM 클라이언트 (OpenAI 호환 chat completions, 구조화 출력).

open-jev/fin-decision 의 NimLLM 을 옮기고 nasdaq_checker 의 대체 모델·reasoning_content 처리를 더했다.
- JSON 스키마를 프롬프트와 response_format 양쪽에 넣고, 응답은 pydantic 으로 검증한다. 형식이 틀리면 재시도.
- 키(NVIDIA_API_KEY, NVIDIA_API_KEY_2) 를 번갈아 쓰고 키마다 분당 NIM_RPM(무료 40) 으로 간격을 둔다.
- 기본 모델이 계속 실패하면(410 단종 등) NIM_FALLBACK_MODEL 로 한 번 더 시도한다.
- 사고(thinking) 출력을 끄고(enable_thinking=False) content 가 비면 reasoning_content 에서 JSON 을 찾는다.
- 같은 (모델, 프롬프트, 스키마) 는 캐시(stock.cache, 12시간)에서 바로 돌려준다.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Callable, TypeVar

import httpx
from pydantic import BaseModel

log = logging.getLogger("kaspflow.llm")
T = TypeVar("T", bound=BaseModel)
NIM_BASE_URL = os.environ.get("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
DEFAULT_MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"
FALLBACK_MODEL = "z-ai/glm-5.3-flash"
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
CACHE_TTL = 12 * 3600
_THINK = re.compile(r"<think>.*?</think>", re.S)


def extract_json(text: str) -> str:
    text = _THINK.sub("", text or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("응답에 JSON 객체가 없습니다")
    return text[start:end + 1]


class LLMUnavailable(RuntimeError):
    pass


class NimLLM:
    def __init__(self, keys: list[str], model: str = DEFAULT_MODEL, fallback: str | None = FALLBACK_MODEL,
                 rpm: int = 40, timeout_s: float = 120, max_retries: int = 3, cache=None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.keys = [k for k in keys if k]
        self.model, self.fallback = model, fallback
        self.interval = 60.0 / rpm if rpm > 0 else 0.0
        self.next_at = [0.0] * max(1, len(self.keys))
        self.turn = 0
        self.lock = asyncio.Lock()
        self.timeout_s, self.max_retries, self.cache = timeout_s, max_retries, cache
        self.transport = transport
        self.calls = 0

    @classmethod
    def from_env(cls, cache=None) -> "NimLLM":
        keys = [os.environ.get(k, "") for k in ("NVIDIA_API_KEY", "NVIDIA_API_KEY_2")]
        return cls(keys, model=os.environ.get("NIM_MODEL") or DEFAULT_MODEL,
                   fallback=os.environ.get("NIM_FALLBACK_MODEL", FALLBACK_MODEL) or None,
                   rpm=int(os.environ.get("NIM_RPM", "40")), cache=cache)

    @property
    def enabled(self) -> bool:
        return bool(self.keys)

    async def _key(self) -> str:
        async with self.lock:
            i = min(range(len(self.keys)), key=lambda j: (self.next_at[j], (j - self.turn) % len(self.keys)))
            self.turn = i + 1
            now = time.monotonic()
            wait = max(0.0, self.next_at[i] - now)
            self.next_at[i] = max(now, self.next_at[i]) + self.interval
        if wait:
            await asyncio.sleep(wait)
        return self.keys[i]

    async def _call(self, client: httpx.AsyncClient, model: str, system: str, prompt: str, schema: type[T],
                    max_tokens: int, json_mode: bool) -> T:
        js = schema.model_json_schema()
        user = (f"{prompt}\n\n[출력 형식]\n아래 JSON 스키마를 만족하는 JSON 객체 하나만 출력한다. "
                f"설명 문장이나 코드 블록 표시는 붙이지 않는다.\n{json.dumps(js, ensure_ascii=False)}")
        body: dict[str, Any] = {"model": model, "temperature": 0.2, "top_p": 0.9, "max_tokens": max_tokens,
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                "chat_template_kwargs": {"enable_thinking": False}}
        if json_mode:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": js}}
        key = await self._key()
        self.calls += 1
        r = await client.post("/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
        if r.status_code in RETRY_STATUS:
            raise RuntimeError(f"HTTP {r.status_code}")
        if r.status_code == 400 and json_mode:
            raise ValueError("json_schema 미지원")  # 다음 시도는 프롬프트만으로
        r.raise_for_status()
        choice = r.json()["choices"][0]
        msg = choice.get("message") or {}
        text = msg.get("content") or msg.get("reasoning_content") or ""
        if choice.get("finish_reason") == "length" and "}" not in text:
            raise RuntimeError("응답이 잘렸습니다")
        return schema.model_validate_json(extract_json(text))

    async def generate(self, role: str, system: str, prompt: str, schema: type[T], max_tokens: int = 4096) -> T:
        if not self.enabled:
            raise LLMUnavailable("NVIDIA_API_KEY 가 설정되지 않았습니다")
        ck = "llm:" + hashlib.sha256(json.dumps([self.model, system, prompt, schema.model_json_schema()],
                                                ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if self.cache is not None:
            hit = await asyncio.to_thread(self.cache.get, ck, CACHE_TTL)
            if hit is not None:
                return schema.model_validate(hit)
        last: Exception | None = None
        models = [self.model] + ([self.fallback] if self.fallback and self.fallback != self.model else [])
        async with httpx.AsyncClient(base_url=NIM_BASE_URL, timeout=self.timeout_s, transport=self.transport) as client:
            for model in models:
                json_mode = True
                for attempt in range(self.max_retries):
                    started = time.monotonic()
                    try:
                        out = await self._call(client, model, system, prompt, schema, max_tokens, json_mode)
                        log.info(json.dumps({"event": "llm_ok", "role": role, "model": model, "attempt": attempt,
                                             "latency_s": round(time.monotonic() - started, 2)}))
                        if self.cache is not None:
                            await asyncio.to_thread(self.cache.set, ck, out.model_dump(mode="json"))
                        return out
                    except Exception as exc:  # noqa: BLE001 - 재시도 후 마지막 오류를 올린다
                        last = exc
                        if isinstance(exc, ValueError) and "json_schema" in str(exc):
                            json_mode = False
                        log.warning(json.dumps({"event": "llm_retry", "role": role, "model": model, "attempt": attempt,
                                                "error": repr(exc)[:200]}, ensure_ascii=False))
                        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (404, 410):
                            break  # 단종/없는 모델: 대체 모델로
                        await asyncio.sleep(min(2 ** attempt, 8))
        raise LLMUnavailable(f"{role} 응답 실패: {last!r}"[:300])


class ScriptedLLM:
    """테스트 대역: fn(role, prompt, schema) -> dict 또는 스키마 객체."""

    def __init__(self, fn: Callable[[str, str, type[BaseModel]], Any]):
        self.fn = fn
        self.calls: list[str] = []
        self.model = "scripted"
        self.enabled = True

    async def generate(self, role, system, prompt, schema, max_tokens=4096):
        self.calls.append(role)
        out = self.fn(role, prompt, schema)
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, schema) else schema.model_validate(out)
