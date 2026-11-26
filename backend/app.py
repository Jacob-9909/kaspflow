"""kaspflow 주식 매매 검증기 API (FastAPI).

  GET  /health                 헬스체크 (DB 까지). 자동배포가 '"status":"ok"' 를 본다
  GET  /search?q=              미국 주식·ETF(+코인) 종목 검색
  GET  /quote/{symbol}         분석 전 화면: 이름·가격·1년 캔들
  GET  /analyze/{symbol}       검증·시뮬레이션 실행 (SSE 진행 스트림, 로그인 필요)
  GET  /runs                   실행 기록 + 판단 뒤 수익률·적중 여부
  GET  /runs/{id}              저장된 결과 전체
  GET  /watchlist              관심 종목과 최근 판단 (POST·DELETE /watchlist 는 로그인 필요)
  GET  /market/leaders         업종 ETF 11개의 상위 보유 5종목: 시세와 기본 지표 (NIM·백테스트 없음, 30분 캐시)
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
from starlette.datastructures import MutableHeaders
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

import engine_backtest
import overview
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
AUTORUN_RETRY_S, AUTORUN_RETRIES = 900.0, 3   # 자동 판단이 실패하면 15분 뒤 다시, 한 시각에 세 번까지
TUNE_EVERY = timedelta(days=7)    # 가중치 자동 조정 주기 (한 주). 결과는 그때의 데이터만으로 정해져 자주 돌려도 조정이 쌓이지 않는다


class State:
    db: DB | None = None
    llm: NimLLM | None = None
    sem: asyncio.Semaphore | None = None
    tasks: set = set()
    login_fail: dict[str, deque] = defaultdict(deque)
    autorun: asyncio.Task | None = None
    weights: dict | None = None    # {"version": id, "weights": {기간: {모듈: w}}}. None 이면 verdict.WEIGHTS
    tuning: bool = False
    rate: dict[str, deque] = defaultdict(deque)   # 공개 조회 경로의 IP 별 요청 시각
    ops: dict = {}                 # 운영 상태 (/health 로 내보낸다): 기동 시각, 마지막 자동 판단·조정, 마지막 오류


S = State()


class NullStore(market.NullCache):
    def ping(self):
        return False


def effective_weights(row: dict) -> dict:
    """저장된 조정 결과에서 실제로 쓸 가중치. 채택된 기간만 저장된 값을 쓰고, 채택되지 않은 기간(과 초기화)은 지금 코드의 기본값을 쓴다.
    채택 안 된 기간에 저장된 값은 그때 기본값의 사본이라, 그대로 쓰면 기본값을 고쳐도 다음 조정까지 옛 값으로 판단한다."""
    rep = (row.get("report") or {}).get("horizons") or {}
    stored = row.get("weights") or {}
    return {h: stored[h] if (rep.get(h) or {}).get("adopted") and h in stored else vd.WEIGHTS[h] for h in vd.WEIGHTS}


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
        S.weights = {"version": last[0]["id"], "weights": effective_weights(last[0])} if last else None
    except Exception as exc:  # noqa: BLE001 - 표가 아직 없거나 DB 가 늦으면 기본값으로
        log.warning("weights load failed: %r", exc)
    S.ops = {"started_at": datetime.now(timezone.utc).isoformat()}
    log.info("kaspflow up: version=%s llm=%s model=%s weights=%s", VERSION, S.llm.enabled, S.llm.model, (S.weights or {}).get("version"))
    if os.environ.get("AUTORUN", "1") != "0":
        S.autorun = asyncio.create_task(_autorun_loop())
    yield
    if S.autorun:
        S.autorun.cancel()
    S.db.close()


VERSION = (os.environ.get("GIT_SHA") or "dev")[:7]   # 이미지를 만든 커밋 (publish-images.yml 이 넣는다)
# API 문서 화면은 공개 서버에서 끈다. 숨긴다고 보호되는 것은 아니고(쓰기 경로는 로그인으로 막는다) 노출면을 줄일 뿐이다. 로컬에서는 API_DOCS=1
_DOCS = os.environ.get("API_DOCS") == "1"
app = FastAPI(title="kaspflow 주식 매매 검증기", version="2.0", lifespan=lifespan,
              docs_url="/docs" if _DOCS else None, redoc_url=None, openapi_url="/openapi.json" if _DOCS else None)

# OWASP Secure Headers Project 권장값 가운데 JSON API 에 해당하는 것. 화면(HTML)의 CSP 는 dashboard/nginx.conf 가 붙인다
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                    "Cross-Origin-Resource-Policy": "same-origin", "X-Permitted-Cross-Domain-Policies": "none"}


class SecurityHeaders:
    """응답 머리에 보안 헤더를 붙인다. 본문 흐름은 건드리지 않는 순수 ASGI 라 실행 화면의 SSE 가 그대로 흐른다."""

    def __init__(self, app):   # Starlette 가 app= 로 넘긴다
        self.inner = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in SECURITY_HEADERS.items():
                    headers.setdefault(k, v)
                if scope.get("scheme") == "https":   # uvicorn --proxy-headers 가 X-Forwarded-Proto 로 맞춰 준다
                    headers.setdefault("Strict-Transport-Security", "max-age=31536000")
            await send(message)
        await self.inner(scope, receive, send_with_headers)


app.add_middleware(SecurityHeaders)


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


PUBLIC_RATE = (60, 60.0)   # 바깥(yfinance)을 부르는 공개 조회는 IP 마다 60초에 60번까지


def _throttle(request: Request) -> None:
    """시세·검색 같은 공개 조회의 남용 방지 (로그인 잠금과 같은 방식). 넘으면 429."""
    limit, window = PUBLIC_RATE
    if len(S.rate) > 5000:   # 주소가 끝없이 쌓이지 않게
        S.rate.clear()
    q, now = S.rate[_client_ip(request)], time.time()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "요청이 너무 많습니다. 잠시 뒤 다시 시도하세요.")
    q.append(now)


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
    # status 는 배포 스크립트가 보는 값(DB 까지 연결됐는가)이다. 나머지는 사람이 운영 상태를 보는 용도
    body = {"status": "ok" if db_ok else "degraded", "db": db_ok, "llm": bool(S.llm and S.llm.enabled),
            "model": S.llm.model if S.llm else None, "running": len(S.tasks), "version": VERSION,
            "started_at": S.ops.get("started_at"),
            "autorun": {"last_at": S.ops.get("autorun_at"), "failed": S.ops.get("autorun_failed", []),
                        "next_at": _slot(datetime.now(timezone.utc), 1).isoformat()},
            "tuning": {"last_at": S.ops.get("tune_at"), "running": S.tuning}, "last_error": S.ops.get("last_error")}
    return JSONResponse(body, status_code=200 if db_ok else 503)


@app.get("/market/leaders")
async def market_leaders():
    """업종별 대표 종목: 업종 ETF 가 가장 많이 담은 5종목의 등락, 200일선·52주 고점 대비, PER, 목표가 대비 (30분 캐시)."""
    try:
        return await asyncio.to_thread(overview.sector_leaders, _store())
    except LookupError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/market")
async def market_overview():
    """시장 정세: 지수, 변동성, 금리, 업종, 다른 자산 (30분 캐시)."""
    try:
        return await asyncio.to_thread(overview.market_overview, _store())
    except LookupError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/search")
async def search(request: Request, q: str = Query("", max_length=40)):
    _throttle(request)
    try:
        return await asyncio.to_thread(market.search, q, _store())
    except Exception as exc:  # noqa: BLE001 - 검색 실패는 빈 목록
        log.warning("search %s: %r", q, exc)
        return []


@app.get("/quote/{symbol}")
async def quote(symbol: str, request: Request):
    _throttle(request)
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


async def _save(run_id: str, data: dict) -> bool:
    """실행 결과 저장. DB 가 잠깐 안 될 때를 대비해 한 번 더 해 본다 (결과는 다시 만들려면 1분과 LLM 호출 9번이 든다)."""
    for attempt in (1, 2):
        try:
            await asyncio.to_thread(S.db.save_run, run_id, data)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("save_run failed (attempt %s) %s: %r", attempt, data.get("symbol"), exc)
            if attempt == 1:
                await asyncio.sleep(1.0)
    return False


async def _job(symbol: str, horizon: str, position: dict | None, queue: asyncio.Queue):
    """분석 작업. 클라이언트가 끊겨도 끝까지 돌고 저장한다 (queue 에 넣기만 하고 아무도 안 읽어도 된다)."""
    async with S.sem:
        try:
            async for ev in pipeline.run(symbol, horizon, position, _store(), S.llm, weights=S.weights):
                if ev["type"] == "result":
                    run_id = str(uuid.uuid4())
                    if not await _save(run_id, ev["data"]):   # 저장 실패해도 결과는 보여 준다
                        run_id = None
                    ev = {"type": "done", "run_id": run_id, "data": ev["data"]}
                await queue.put(ev)
        except LookupError as exc:
            await queue.put({"type": "error", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log.exception("analysis failed %s", symbol)
            # 내부 예외 내용은 로그에만 남기고 화면에는 종류만 알린다
            await queue.put({"type": "error", "message": f"분석 중 오류가 났습니다 ({type(exc).__name__}). 잠시 뒤 다시 실행하세요."})
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
                if not await _save(str(uuid.uuid4()), ev["data"]):
                    raise RuntimeError("자동 판단 결과를 저장하지 못했습니다")


async def _autorun_loop() -> None:
    """관심 종목 자동 판단. 평일 장 마감 뒤 하루 한 번, 한 종목씩 차례로 돈다 (NIM 호출이 몰리지 않게).
    다시 뜨면 밀린 것부터 처리한다. 실패한 종목은 다음 시각에 다시 시도한다."""
    await asyncio.sleep(60)   # 기동 직후 자동배포 헬스체크와 겹치지 않게
    retries = 0
    while True:
        failed = await _autorun_once()
        try:
            last = await asyncio.to_thread(S.db.weights_history, 1)
            if not last or datetime.now(timezone.utc) - last[0]["created_at"] > TUNE_EVERY:
                await _tune()
                S.ops["tune_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:  # noqa: BLE001 - 조정이 실패해도 지금 가중치로 계속 판단한다
            log.warning("tuning failed: %r", exc)
            S.ops["last_error"] = f"tuning: {exc!r}"[:200]
        try:
            await asyncio.to_thread(S.db.prune_cache)
        except Exception as exc:  # noqa: BLE001
            log.warning("cache prune failed: %r", exc)
        now = datetime.now(timezone.utc)
        wait = (_slot(now, 1) - now).total_seconds()
        # 실패가 있으면 다음 날까지 기다리지 않고 곧 다시 본다 (이미 돈 종목은 autorun_due 가 건너뛴다). 계속 실패하면 그만두고 다음 시각에
        retries = retries + 1 if failed and retries < AUTORUN_RETRIES else 0
        await asyncio.sleep(max(60.0, min(wait, AUTORUN_RETRY_S) if retries else wait))


async def _autorun_once() -> list[str]:
    """밀린 관심 종목을 한 번 돈다. 실패한 종목 이름을 돌려준다 (DB 를 못 읽으면 ["*"])."""
    failed: list[str] = []
    try:
        items = await asyncio.to_thread(S.db.list_watchlist)
        for w in autorun_due(items, datetime.now(timezone.utc)):
            try:
                await _autorun_symbol(w["symbol"], w["horizon"])
                log.info("autorun ok %s %s", w["symbol"], w["horizon"])
            except Exception as exc:  # noqa: BLE001 - 한 종목이 실패해도 나머지는 돈다
                log.warning("autorun failed %s: %r", w["symbol"], exc)
                failed.append(w["symbol"])
                S.ops["last_error"] = f"autorun {w['symbol']}: {exc!r}"[:200]
    except Exception as exc:  # noqa: BLE001 - DB 가 잠깐 안 될 때
        log.warning("autorun check failed: %r", exc)
        failed = ["*"]
        S.ops["last_error"] = f"autorun: {exc!r}"[:200]
    S.ops.update(autorun_at=datetime.now(timezone.utc).isoformat(), autorun_failed=failed)
    return failed


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
        async with S.sem:   # 대형주 31개 x 10년 백테스트라 무겁다: 분석 두 개와 겹쳐 메모리 한도를 넘지 않게 실행 칸을 하나 잡는다
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


@app.get("/watchlist/overview")
async def watchlist_overview():
    """관심 종목 한눈에 보기: 종목마다 가장 최근 판단의 기간별 판단, 모듈 점수, 지금 할 일."""
    rows = await asyncio.to_thread(S.db.watch_overview)
    return {"items": [{**r, "created_at": r["created_at"] and r["created_at"].isoformat()} for r in rows]}


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
def _price_on(symbol: str, day: datetime, memo: dict | None = None) -> float | None:
    """평가일(이후 첫 거래일) 종가. 10년 가격 캐시가 있으면 쓴다 (과거 구간은 바뀌지 않으니 오래된 캐시도 유효).
    memo 를 주면 종목별 종가 열을 거기에 두고 다시 쓴다 (기록 화면은 줄마다 부르는데 캐시 한 건이 수백 KB 다)."""
    memo = {} if memo is None else memo
    if symbol not in memo:
        p = _store().get(f"yf:prices:{symbol}", 400 * 86400)
        memo[symbol] = market.close_series(p) if p else None
    s = memo[symbol]
    if s is None:
        return None
    after = s[s.index >= pd.Timestamp(day.date())]
    return float(after.iloc[0]) if len(after) else None


@app.get("/runs")
async def runs(symbol: str | None = Query(None, max_length=16), limit: int = Query(50, ge=1, le=200),
               source: str | None = Query(None, pattern="^(auto|manual)$")):
    """source: auto 는 관심 종목 자동 판단만, manual 은 직접 실행한 것만, 없으면 전부."""
    try:
        sym = market.clean_symbol(symbol) if symbol else None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    rows = await asyncio.to_thread(S.db.list_runs, sym, limit, source)
    closes: dict = {}
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
        end_px = await asyncio.to_thread(_price_on, r["symbol"], due, closes) if evaluated else last.get(r["symbol"])
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
