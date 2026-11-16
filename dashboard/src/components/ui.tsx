/** 여러 화면이 같이 쓰는 작은 조각. */
import type { ReactNode } from "react";

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
