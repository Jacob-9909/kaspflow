/** 접근성 있는 탭 막대: 방향키로 이동, 비활성 탭은 이유를 툴팁으로, 오른쪽에 개수 배지. */
import { useRef } from "react";

export interface TabDef { key: string; label: string; badge?: string | number; disabled?: boolean; hint?: string }

export function TabBar({ tabs, active, onChange, label }: { tabs: TabDef[]; active: string; onChange: (k: string) => void; label: string }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const enabled = tabs.map((t, i) => (t.disabled ? -1 : i)).filter((i) => i >= 0);
  const move = (from: number, dir: 1 | -1) => {
    const pos = enabled.indexOf(from);
    const next = enabled[(pos + dir + enabled.length) % enabled.length];
    refs.current[next]?.focus();
    onChange(tabs[next].key);
  };
  return (
    <nav className="tabbar" aria-label={label}>
      <div role="tablist" aria-label={label}>
        {tabs.map((t, i) => (
          <button key={t.key} ref={(el) => { refs.current[i] = el; }} role="tab" id={`tab-${t.key}`} aria-controls={`panel-${t.key}`}
                  aria-selected={active === t.key} aria-disabled={t.disabled || undefined} tabIndex={active === t.key ? 0 : -1}
                  title={t.disabled ? t.hint : undefined} className={active === t.key ? "on" : ""}
                  onClick={() => !t.disabled && onChange(t.key)}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowRight") { e.preventDefault(); move(i, 1); }
                    else if (e.key === "ArrowLeft") { e.preventDefault(); move(i, -1); }
                    else if (e.key === "Home") { e.preventDefault(); refs.current[enabled[0]]?.focus(); onChange(tabs[enabled[0]].key); }
                    else if (e.key === "End") { e.preventDefault(); const last = enabled[enabled.length - 1]; refs.current[last]?.focus(); onChange(tabs[last].key); }
                  }}>
            {t.label}
            {t.badge !== undefined && t.badge !== "" && <span className="badge">{t.badge}</span>}
          </button>
        ))}
      </div>
    </nav>
  );
}
