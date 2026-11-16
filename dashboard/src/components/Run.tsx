/** 실행 패널(기간·보유 정보·실행 버튼·로그인)과 진행 타임라인. */
import { useState } from "react";
import { api, type Horizon } from "../api";
import { HORIZON_LABEL, HORIZONS } from "../format";

export interface StepState { key: string; label: string; status: "wait" | "running" | "done"; detail?: string; ms?: number }

export function LoginForm({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api.login(pw);
      setPw("");
      onDone();
    } catch (x) {
      setErr((x as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="login" onSubmit={submit}>
      <label htmlFor="pw">실행 비밀번호</label>
      <div className="row">
        <input id="pw" type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password"
               aria-describedby={err ? "pw-err" : undefined} aria-invalid={!!err} required />
        <button className="btn" disabled={busy || !pw}>{busy ? "확인 중…" : "로그인"}</button>
      </div>
      {err && <p className="field-error" id="pw-err" role="alert">{err}</p>}
      <p className="hint">결과·기록 보기는 누구나 됩니다. 실행은 NIM 호출량 보호를 위해 비밀번호가 필요합니다.</p>
    </form>
  );
}

interface Saved { horizon: Horizon; qty: string; avg: string }

/** 종목별 마지막 실행 설정 (기간·보유 정보). 브라우저에만 저장한다. */
function loadSaved(symbol: string): Saved {
  try {
    const raw = localStorage.getItem(`kf-run:${symbol}`);
    if (raw) return JSON.parse(raw) as Saved;
    const h = localStorage.getItem("kf-horizon") as Horizon | null;
    return { horizon: h ?? "mid", qty: "", avg: "" };
  } catch {
    return { horizon: "mid", qty: "", avg: "" };
  }
}

function save(symbol: string, v: Saved) {
  try {
    localStorage.setItem(`kf-run:${symbol}`, JSON.stringify(v));
    localStorage.setItem("kf-horizon", v.horizon);
  } catch { /* 저장 못 해도 실행에는 문제없다 */ }
}

export function RunPanel({ symbol, authed, loginEnabled, running, onRun, onLogin, horizonHint }: {
  symbol: string; authed: boolean; loginEnabled: boolean; running: boolean;
  onRun: (h: Horizon, pos: { qty: number; avg: number } | null) => void; onLogin: () => void;
  horizonHint?: Horizon;  // 저장된 결과를 보고 있으면 그 결과의 기간으로 시작한다
}) {
  const [init] = useState(() => ({ ...loadSaved(symbol), ...(horizonHint ? { horizon: horizonHint } : {}) }));
  const [horizon, setHorizon] = useState<Horizon>(init.horizon);
  const [qty, setQty] = useState(init.qty);
  const [avg, setAvg] = useState(init.avg);
  const q = Number(qty), a = Number(avg);
  const pos = qty && avg && q > 0 && a > 0 ? { qty: q, avg: a } : null;
  const posInvalid = (qty || avg) && !pos;
  return (
    <section className="card run-panel" id="run-panel" aria-labelledby="run-title">
      <h2 id="run-title">검증·시뮬레이션</h2>
      <fieldset className="segmented">
        <legend>판단 기간</legend>
        {HORIZONS.map((h) => (
          <label key={h} className={horizon === h ? "on" : ""}>
            <input type="radio" name="horizon" value={h} checked={horizon === h} onChange={() => setHorizon(h)} />
            {HORIZON_LABEL[h]}
          </label>
        ))}
      </fieldset>
      <fieldset className="position">
        <legend>보유 중이면 입력 (선택)</legend>
        <label>수량<input inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} placeholder="0" /></label>
        <label>평균 단가($)<input inputMode="decimal" value={avg} onChange={(e) => setAvg(e.target.value)} placeholder="0.00" /></label>
        {(qty || avg) && <button type="button" className="link" onClick={() => { setQty(""); setAvg(""); }}>지우기</button>}
      </fieldset>
      {posInvalid && <p className="field-error" role="alert">수량과 평균 단가를 둘 다 0보다 크게 입력하세요.</p>}
      {authed ? (
        <button className="btn primary run-btn" id="run-btn" disabled={running || !!posInvalid}
                onClick={() => { save(symbol, { horizon, qty, avg }); onRun(horizon, pos); }}>
          {running ? <><span className="spinner" aria-hidden="true" /> 실행 중…</> : "검증·시뮬레이션 실행"}
        </button>
      ) : loginEnabled ? <LoginForm onDone={onLogin} /> : <p className="hint">서버에 APP_PASSWORD 가 설정되지 않아 실행할 수 없습니다.</p>}
      <p className="hint">전략 11종 × 변형 30개 백테스트, 워크포워드·순열 검정·DSR, 몬테카를로 2,000경로, 재무·밸류에이션, 뉴스 감성, 투자 대가 6인을 차례로 돌려 종합 판단을 냅니다. 약 30~90초.</p>
    </section>
  );
}

export function Progress({ steps, queued }: { steps: StepState[]; queued: boolean }) {
  return (
    <section className="card progress" aria-labelledby="prog-title" aria-live="polite">
      <h2 id="prog-title">진행 상황{queued && <span className="tag">대기열</span>}</h2>
      <ol>
        {steps.map((s) => (
          <li key={s.key} className={s.status}>
            <span className="dot" aria-hidden="true">{s.status === "done" ? <svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" /></svg> : s.status === "running" ? <span className="spinner small" /> : null}</span>
            <div>
              <div className="step-label">{s.label}{s.ms !== undefined && <span className="ms">{(s.ms / 1000).toFixed(1)}초</span>}</div>
              {s.detail && <div className="step-detail">{s.detail}</div>}
            </div>
            <span className="sr-only">{s.status === "done" ? "완료" : s.status === "running" ? "진행 중" : "대기"}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
