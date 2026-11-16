"""kaspflow 주식 매매 검증기 API (FastAPI).

  GET  /health                 헬스체크 (DB 까지). 자동배포가 '"status":"ok"' 를 본다
  GET  /search?q=              미국 주식·ETF(+코인) 종목 검색
  GET  /quote/{symbol}         분석 전 화면: 이름·가격·1년 캔들
  GET  /analyze/{symbol}       검증·시뮬레이션 실행 (SSE 진행 스트림, 로그인 필요)
  GET  /runs                   실행 기록 + 판단 뒤 수익률·적중 여부
  GET  /runs/{id}              저장된 결과 전체
  POST /auth/login|logout, GET /auth/me   비밀번호 로그인 (APP_PASSWORD)

실행만 로그인이 필요하고 결과 보기는 공개다 (2026-11-16 결정: NIM 호출 할당량 보호).
분석은 클라이언트와 분리된 작업으로 돈다. 브라우저를 닫아도 끝까지 돌고 기록에 저장된다.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

import market
import pipeline
from db import DB
from llm import NimLLM

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("kaspflow.app")

COOKIE = "kf_session"
SESSION_S = 14 * 86400
HORIZON_DAYS = {"short": 30, "mid": 91, "long": 365}   # 적중 평가까지 기다리는 달력 일수
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_RUNS", "2"))


class State:
    db: DB | None = None
    llm: NimLLM | None = None
    sem: asyncio.Semaphore | None = None
    tasks: set = set()
    login_fail: dict[str, deque] = defaultdict(deque)


S = State()


class NullStore(market.NullCache):
    def ping(self):
        return False


@asynccontextmanager
async def lifespan(_: FastAPI):
    S.sem = asyncio.Semaphore(MAX_CONCURRENT)
    S.db = DB()
    S.db.open()
    try:
        await asyncio.to_thread(S.db.init_schema)
    except Exception as exc:  # noqa: BLE001 - DB 가 늦게 뜨면 /health 가 degraded 로 알린다
        log.error("schema init failed: %r", exc)
    S.llm = NimLLM.from_env(cache=S.db)
    log.info("kaspflow up: llm=%s model=%s", S.llm.enabled, S.llm.model)
    yield
    S.db.close()


app = FastAPI(title="kaspflow 주식 매매 검증기", version="2.0", lifespan=lifespan)


def _store():
    return S.db if S.db is not None else NullStore()


# ------------------------------------------------------------
# 인증 (비밀번호 1개, HMAC 서명 쿠키)
# ------------------------------------------------------------
def _password() -> str:
    return os.environ.get("APP_PASSWORD", "")


# 서명 키 = HMAC(서버 비밀값, 비밀번호). 비밀번호만으로 키를 만들면 코드가 공개라서, 짧은 비밀번호는 쿠키를 만들어
# /auth/me 에 대 보는 식으로 로그인 잠금 없이 맞힐 수 있다. 비밀번호를 바꾸면 기존 세션이 모두 끊긴다.
# ponytail: SESSION_SECRET 이 없으면 프로세스마다 무작위 값이라 재시작하면 다시 로그인해야 한다 (워커가 여럿이면 필수).
_SERVER_KEY = os.environ.get("SESSION_SECRET", "").encode() or secrets.token_bytes(32)


def _secret() -> bytes:
    return hmac.new(_SERVER_KEY, _password().encode(), hashlib.sha256).digest()


def make_token(now: float | None = None) -> str:
    exp = int((time.time() if now is None else now) + SESSION_S)
    return f"{exp}.{hmac.new(_secret(), str(exp).encode(), hashlib.sha256).hexdigest()}"


def token_ok(token: str | None) -> bool:
    if not token or not _password() or "." not in token:
        return False
    exp, sig = token.split(".", 1)
    good = hmac.new(_secret(), exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and exp.isdigit() and int(exp) > time.time()


def _client_ip(req: Request) -> str:
    fwd = req.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (req.client.host if req.client else "?")


class Login(BaseModel):
    password: str


@app.post("/auth/login")
def login(body: Login, request: Request, response: Response):
    if not _password():
        raise HTTPException(503, "서버에 APP_PASSWORD 가 설정되지 않았습니다")
    ip = _client_ip(request)
    fails = S.login_fail[ip]
    while fails and fails[0] < time.time() - 600:
        fails.popleft()
    if len(fails) >= 5:
        raise HTTPException(429, "로그인 시도가 너무 많습니다. 10분 뒤 다시 시도하세요")
    if not hmac.compare_digest(body.password.encode(), _password().encode()):
        fails.append(time.time())
        raise HTTPException(401, "비밀번호가 틀렸습니다")
    secure = request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
    response.set_cookie(COOKIE, make_token(), max_age=SESSION_S, httponly=True, samesite="lax", secure=secure, path="/")
    return {"ok": True}


@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/auth/me")
def me(request: Request):
    return {"authenticated": token_ok(request.cookies.get(COOKIE)), "login_enabled": bool(_password()),
            "llm": bool(S.llm and S.llm.enabled)}


# ------------------------------------------------------------
# 조회
# ------------------------------------------------------------
@app.get("/health")
def health():
    db_ok = S.db.ping() if S.db else False
    body = {"status": "ok" if db_ok else "degraded", "db": db_ok, "llm": bool(S.llm and S.llm.enabled),
            "model": S.llm.model if S.llm else None, "running": len(S.tasks)}
    return JSONResponse(body, status_code=200 if db_ok else 503)


@app.get("/search")
async def search(q: str = Query("", max_length=40)):
    try:
        return await asyncio.to_thread(market.search, q, _store())
    except Exception as exc:  # noqa: BLE001 - 검색 실패는 빈 목록
        log.warning("search %s: %r", q, exc)
        return []


@app.get("/quote/{symbol}")
async def quote(symbol: str):
    try:
        sym = market.clean_symbol(symbol)
        return await asyncio.to_thread(market.quote, sym, _store())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


# ------------------------------------------------------------
# 실행 (SSE)
# ------------------------------------------------------------
def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False, default=str)}\n\n"


async def _job(symbol: str, horizon: str, position: dict | None, queue: asyncio.Queue):
    """분석 작업. 클라이언트가 끊겨도 끝까지 돌고 저장한다 (queue 에 넣기만 하고 아무도 안 읽어도 된다)."""
    async with S.sem:
        try:
            async for ev in pipeline.run(symbol, horizon, position, _store(), S.llm):
                if ev["type"] == "result":
                    run_id = str(uuid.uuid4())
                    try:
                        await asyncio.to_thread(S.db.save_run, run_id, ev["data"])
                    except Exception as exc:  # noqa: BLE001 - 저장 실패해도 결과는 보여 준다
                        log.error("save_run failed: %r", exc)
                        run_id = None
                    ev = {"type": "done", "run_id": run_id, "data": ev["data"]}
                await queue.put(ev)
        except LookupError as exc:
            await queue.put({"type": "error", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log.exception("analysis failed %s", symbol)
            await queue.put({"type": "error", "message": f"분석 중 오류: {exc!r}"[:300]})
        finally:
            await queue.put(None)


@app.get("/analyze/{symbol}")
async def analyze(symbol: str, request: Request, horizon: str = Query("mid", pattern="^(short|mid|long)$"),
                  qty: float | None = Query(None, ge=0, le=1e9), avg: float | None = Query(None, gt=0, le=1e7)):
    if not token_ok(request.cookies.get(COOKIE)):
        raise HTTPException(401, "실행하려면 로그인이 필요합니다")
    if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
        raise HTTPException(403, "다른 사이트에서 온 실행 요청은 받지 않습니다")
    try:
        sym = market.clean_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    position = {"qty": qty, "avg_price": avg} if qty and avg else None
    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(_job(sym, horizon, position, queue))
    S.tasks.add(task)
    task.add_done_callback(S.tasks.discard)

    async def stream():
        yield _sse({"type": "start", "symbol": sym, "horizon": horizon, "steps": pipeline.STEPS,
                    "queued": S.sem.locked()})
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": ping\n\n"   # 프록시가 연결을 끊지 않게
                continue
            if ev is None:
                break
            yield _sse(ev)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------
# 기록 · 적중률
# ------------------------------------------------------------
def judge(verdict: str, ret: float) -> bool:
    """midas-touch 판단 원장과 같은 기준: 매수는 +2% 초과, 매도는 -2% 미만, 관망은 ±5% 이내면 적중."""
    return ret > 0.02 if verdict == "buy" else ret < -0.02 if verdict == "sell" else abs(ret) <= 0.05


def _price_on(symbol: str, day: datetime) -> float | None:
    """평가일(이후 첫 거래일) 종가. 10년 가격 캐시가 있으면 쓴다 (과거 구간은 바뀌지 않으니 오래된 캐시도 유효)."""
    p = _store().get(f"yf:prices:{symbol}", 400 * 86400)
    if not p:
        return None
    s = market.close_series(p)
    after = s[s.index >= pd.Timestamp(day.date())]
    return float(after.iloc[0]) if len(after) else None


@app.get("/runs")
async def runs(symbol: str | None = Query(None, max_length=16), limit: int = Query(50, ge=1, le=200)):
    sym = market.clean_symbol(symbol) if symbol else None
    rows = await asyncio.to_thread(S.db.list_runs, sym, limit)
    last = {}
    if rows:
        try:
            last = await asyncio.to_thread(market.last_closes, sorted({r["symbol"] for r in rows}), _store())
        except Exception as exc:  # noqa: BLE001 - 시세 조회 실패 시 수익률만 비운다
            log.warning("last_closes: %r", exc)
    now = datetime.now(timezone.utc)
    out, scored = [], []
    for r in rows:
        due = r["created_at"] + timedelta(days=HORIZON_DAYS[r["horizon"]])
        evaluated = now >= due
        end_px = await asyncio.to_thread(_price_on, r["symbol"], due) if evaluated else last.get(r["symbol"])
        if evaluated and end_px is None:
            end_px = last.get(r["symbol"])
        ret = end_px / r["price"] - 1 if end_px else None
        hit = judge(r["verdict"], ret) if evaluated and ret is not None else None
        if hit is not None:
            scored.append((r["verdict"], hit, ret))
        out.append({**r, "id": str(r["id"]), "created_at": r["created_at"].isoformat(), "due": due.isoformat(),
                    "evaluated": evaluated, "return": None if ret is None else round(ret, 4), "hit": hit,
                    "current": last.get(r["symbol"])})
    stats = {"evaluated": len(scored), "hit_rate": round(sum(h for _, h, _ in scored) / len(scored), 3) if scored else None,
             "by_verdict": {v: {"n": len(xs), "hit_rate": round(sum(h for _, h, _ in xs) / len(xs), 3),
                                "avg_return": round(sum(r for _, _, r in xs) / len(xs), 4)}
                            for v in ("buy", "hold", "sell") if (xs := [x for x in scored if x[0] == v])}}
    return {"runs": out, "stats": stats}


@app.get("/runs/{run_id}")
async def run_detail(run_id: str):
    try:
        uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(400, "잘못된 실행 id") from exc
    r = await asyncio.to_thread(S.db.get_run, run_id)
    if not r:
        raise HTTPException(404, "기록이 없습니다")
    return r
