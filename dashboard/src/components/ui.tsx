/** 여러 화면이 같이 쓰는 작은 조각. */
import { Component, type ErrorInfo, type ReactNode } from "react";

/** 메타 정보 나열. 가운뎃점 대신 가는 세로선으로 구분한다 (빈 값은 건너뜀). */
export function MetaList({ items, className = "" }: { items: ReactNode[]; className?: string }) {
  return (
    <span className={`meta-list ${className}`.trim()}>
      <span>{items.filter((x) => x !== null && x !== undefined && x !== false && x !== "").map((x, i) => <span key={i}>{x}</span>)}</span>
    </span>
  );
}

/** 오른쪽 꺾쇠 (자세히 보기) */
export function Chevron() {
  return <svg className="chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" /></svg>;
}

/** 화면을 그리다 난 오류를 잡아 빈 화면 대신 안내를 보인다.
 *  (예전에 저장된 결과는 지금과 모양이 달라 한 군데가 깨질 수 있다. 오류 경계가 없으면 앱 전체가 하얗게 된다) */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error("화면을 그리지 못했습니다", error, info.componentStack); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="container">
        <section className="card empty" role="alert">
          <h2>화면을 그리지 못했습니다</h2>
          <p>일시적인 문제일 수 있습니다. 새로 고쳐도 같으면 홈에서 다른 종목이나 최근 결과를 열어 보세요.</p>
          <p className="hint">{String(this.state.error.message).slice(0, 200)}</p>
          <div className="tune-actions">
            <button className="btn primary" onClick={() => location.reload()}>새로 고침</button>
            <a className="btn" href="/">홈으로</a>
          </div>
        </section>
      </main>
    );
  }
}
