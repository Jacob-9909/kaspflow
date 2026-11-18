"""kaspflow 주식 매매 검증기 API (FastAPI).

  GET  /health                 헬스체크 (DB 까지). 자동배포가 '"status":"ok"' 를 본다
  GET  /search?q=              미국 주식·ETF(+코인) 종목 검색
  GET  /quote/{symbol}         분석 전 화면: 이름·가격·1년 캔들
  GET  /analyze/{symbol}       검증·시뮬레이션 실행 (SSE 진행 스트림, 로그인 필요)
  GET  /runs                   실행 기록 + 판단 뒤 수익률·적중 여부
  GET  /runs/{id}              저장된 결과 전체
  GET  /watchlist              관심 종목과 최근 판단 (POST·DELETE /watchlist 는 로그인 필요)
  GET  /weights                판단 엔진 가중치 (기본값, 지금 값, 자동 조정 기록). POST /weights/tune|reset 은 로그인 필요
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
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

import engine_backtest
import market
import pipeline
import tuning
import verdict as vd
from verdict import HORIZON_DAYS, judge
from db import DB
from llm import NimLLM

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("kaspflow.app")

COOKIE = "kf_session"
SESSION_S = 14 * 86400
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_RUNS", "2"))
NY = ZoneInfo("America/New_York")
AUTORUN_AT = (16, 40)   # 관심 종목 자동 판단 시각 (뉴욕). 16:00 장 마감 뒤 일봉이 확정되고 나서
WATCH_MAX = int(os.environ.get("WATCHLIST_MAX", "10"))   # 하루 NIM 호출 = 종목 수 x 9
TUNE_EVERY = timedelta(days=28)   # 가중치 자동 조정 주기 (약 한 달)


class State:
    db: DB | None = None
    llm: NimLLM | None = None
    sem: asyncio.Semaphore | None = None
    tasks: set = set()
    login_fail: dict[str, deque] = defaultdict(deque)
    autorun: asyncio.Task | None = None
    weights: dict | None = None    # {"version": id, "weights": {기간: {모듈: w}}}. None 이면 verdict.WEIGHTS
    tuning: bool = False


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
    try:
        last = await asyncio.to_thread(S.db.weights_history, 1)
        S.weights = {"version": last[0]["id"], "weights": last[0]["weights"]} if last else None
    except Exception as exc:  # noqa: BLE001 - 표가 아직 없거나 DB 가 늦으면 기본값으로
        log.warning("weights load failed: %r", exc)
    log.info("kaspflow up: llm=%s model=%s weights=%s", S.llm.enabled, S.llm.model, (S.weights or {}).get("version"))
    if os.environ.get("AUTORUN", "1") != "0":
        S.autorun = asyncio.create_task(_autorun_loop())
    yield
    if S.autorun:
        S.autorun.cancel()
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
            async for ev in pipeline.run(symbol, horizon, position, _store(), S.llm, weights=S.weights):
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
# 관심 종목 · 자동 판단
# ------------------------------------------------------------
def _slot(now: datetime, step: int) -> datetime:
    """평일 16:40(뉴욕) 자동 판단 시각. step=-1 이면 now 이전 가장 최근, +1 이면 now 다음.
    ponytail: 미국 공휴일은 거르지 않는다 (그날은 같은 일봉으로 한 번 더 판단할 뿐)."""
    t = now.astimezone(NY).replace(hour=AUTORUN_AT[0], minute=AUTORUN_AT[1], second=0, microsecond=0)
    if (step < 0 and t > now) or (step > 0 and t <= now):
        t += timedelta(days=step)
    while t.weekday() >= 5:
        t += timedelta(days=step)
    return t


def autorun_due(items: list[dict], now: datetime) -> list[dict]:
    """자동 판단이 밀린 관심 종목: 가장 최근 자동 판단 시각 뒤로 같은 종목·기간의 실행이 없는 것 (수동 실행도 친다)."""
    slot = _slot(now, -1)
    return [w for w in items if not w["last"] or w["last"]["created_at"] < slot]


async def _autorun_symbol(symbol: str, horizon: str) -> None:
    async with S.sem:
        async for ev in pipeline.run(symbol, horizon, None, _store(), S.llm, weights=S.weights):
            if ev["type"] == "result":
                ev["data"]["meta"]["source"] = "auto"
                await asyncio.to_thread(S.db.save_run, str(uuid.uuid4()), ev["data"])


async def _autorun_loop() -> None:
    """관심 종목 자동 판단. 평일 장 마감 뒤 하루 한 번, 한 종목씩 차례로 돈다 (NIM 호출이 몰리지 않게).
    다시 뜨면 밀린 것부터 처리한다. 실패한 종목은 다음 시각에 다시 시도한다."""
    await asyncio.sleep(60)   # 기동 직후 자동배포 헬스체크와 겹치지 않게
    while True:
        try:
            items = await asyncio.to_thread(S.db.list_watchlist)
            for w in autorun_due(items, datetime.now(timezone.utc)):
                try:
                    await _autorun_symbol(w["symbol"], w["horizon"])
                    log.info("autorun ok %s %s", w["symbol"], w["horizon"])
                except Exception as exc:  # noqa: BLE001 - 한 종목이 실패해도 나머지는 돈다
                    log.warning("autorun failed %s: %r", w["symbol"], exc)
        except Exception as exc:  # noqa: BLE001 - DB 가 잠깐 안 될 때
            log.warning("autorun check failed: %r", exc)
        try:
            last = await asyncio.to_thread(S.db.weights_history, 1)
            if not last or datetime.now(timezone.utc) - last[0]["created_at"] > TUNE_EVERY:
                await _tune()
        except Exception as exc:  # noqa: BLE001 - 조정이 실패해도 지금 가중치로 계속 판단한다
            log.warning("tuning failed: %r", exc)
        now = datetime.now(timezone.utc)
        await asyncio.sleep(max(60.0, (_slot(now, 1) - now).total_seconds()))


async def _tune() -> None:
    """가중치 자동 조정: 백테스트(대형주, 10년, 매달) + 기간이 지난 실제 판단 기록 -> tuning.tune -> 기록하고 바로 쓴다."""
    if S.tuning:
        return
    S.tuning = True

    def work() -> dict:
        bt, _ = engine_backtest.collect(cache=_store(), log=lambda *_: None)
        prices: dict[str, pd.Series] = {}

        def price_on(sym: str, day) -> float | None:
            if sym not in prices:
                prices[sym] = market.close_series(market._cached(_store(), f"yf:prices:{sym}", 86400, lambda: market._history(sym, "10y")))
            after = prices[sym][prices[sym].index >= pd.Timestamp(day.date())]
            return float(after.iloc[0]) if len(after) else None

        live = tuning.live_samples(S.db.runs_for_tuning(), price_on)
        w, rep = tuning.tune([{**r, "src": "bt"} for r in bt] + live)
        adopted = any(r["adopted"] for r in rep.values())
        vid = S.db.save_weights(w, adopted, {"horizons": rep, "n_backtest": len(bt), "n_live": len(live)})
        log.info("tuning done: version=%s adopted=%s", vid, {h: r["adopted"] for h, r in rep.items()})
        return {"version": vid, "weights": w}

    try:
        S.weights = await asyncio.to_thread(work)
    finally:
        S.tuning = False


@app.get("/weights")
async def weights():
    hist = await asyncio.to_thread(S.db.weights_history, 6)
    cur = S.weights or {}
    return {"defaults": vd.WEIGHTS, "current": cur.get("weights") or vd.WEIGHTS, "version": cur.get("version"), "tuning": S.tuning,
            "history": [{**r, "created_at": r["created_at"].isoformat()} for r in hist],
            "rules": {"n0": tuning.N0, "min_live": tuning.MIN_LIVE, "margin": tuning.MARGIN, "split": tuning.SPLIT, "every_days": TUNE_EVERY.days}}


@app.post("/weights/tune")
async def weights_tune(request: Request):
    _guard(request)
    if S.tuning:
        raise HTTPException(409, "이미 조정 중입니다")
    task = asyncio.create_task(_tune())
    S.tasks.add(task)
    task.add_done_callback(S.tasks.discard)
    return {"started": True}


@app.post("/weights/reset")
async def weights_reset(request: Request):
    """기본값으로 되돌린다 (다음 자동 조정 때까지). 기록은 남는다."""
    _guard(request)
    vid = await asyncio.to_thread(S.db.save_weights, vd.WEIGHTS, False, {"reset": True})
    S.weights = {"version": vid, "weights": vd.WEIGHTS}
    return {"ok": True}


def _guard(request: Request) -> None:
    if not token_ok(request.cookies.get(COOKIE)):
        raise HTTPException(401, "로그인이 필요합니다")
    if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
        raise HTTPException(403, "다른 사이트에서 온 요청은 받지 않습니다")


class Watch(BaseModel):
    symbol: str
    horizon: str = "mid"


@app.get("/watchlist")
async def watchlist():
    items = await asyncio.to_thread(S.db.list_watchlist)
    iso = lambda r: r and {**r, "created_at": r["created_at"].isoformat()}
    return {"items": [{**w, "added_at": w["added_at"].isoformat(), "last": iso(w["last"]), "prev": iso(w["prev"])} for w in items],
            "next_run": _slot(datetime.now(timezone.utc), 1).isoformat(), "max": WATCH_MAX}


@app.post("/watchlist")
async def watch_add(body: Watch, request: Request):
    _guard(request)
    if body.horizon not in HORIZON_DAYS:
        raise HTTPException(400, "기간은 short, mid, long 중 하나입니다")
    try:
        sym = market.clean_symbol(body.symbol)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    items = await asyncio.to_thread(S.db.list_watchlist)
    if len(items) >= WATCH_MAX and sym not in {w["symbol"] for w in items}:
        raise HTTPException(400, f"관심 종목은 {WATCH_MAX}개까지입니다 (하루 NIM 호출 보호)")
    await asyncio.to_thread(S.db.add_watch, sym, body.horizon)
    return {"ok": True}


@app.delete("/watchlist/{symbol}")
async def watch_remove(symbol: str, request: Request):
    _guard(request)
    try:
        sym = market.clean_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await asyncio.to_thread(S.db.remove_watch, sym)
    return {"ok": True}


# ------------------------------------------------------------
# 기록 · 적중률
# ------------------------------------------------------------
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
