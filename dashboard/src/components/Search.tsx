/** 종목 검색 (자동완성). 입력이 멈추고 250ms 뒤 조회한다. 방향키·엔터로 고를 수 있다. */
import { useEffect, useId, useRef, useState } from "react";
import { api, type SearchHit } from "../api";

export default function Search({ onPick, big = false, autoFocus = false }: { onPick: (symbol: string) => void; big?: boolean; autoFocus?: boolean }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [busy, setBusy] = useState(false);
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const term = q.trim();
    if (!term) { setHits([]); return; }
    let alive = true;
    const t = setTimeout(async () => {
      setBusy(true);
      try {
        const res = await api.search(term);
        if (alive) { setHits(res); setActive(0); setOpen(true); }
      } catch {
        if (alive) setHits([]);
      } finally {
        if (alive) setBusy(false);
      }
    }, 250);
    return () => { alive = false; clearTimeout(t); };
  }, [q]);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const pick = (sym: string) => { setOpen(false); setQ(""); onPick(sym.toUpperCase()); };

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); setOpen(true); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Escape") setOpen(false);
    else if (e.key === "Enter") {
      e.preventDefault();
      if (open && hits[active]) pick(hits[active].symbol);
      else if (/^[A-Za-z0-9.\-^=]{1,15}$/.test(q.trim())) pick(q.trim());
    }
  };

  return (
    <div className={`search${big ? " big" : ""}`} ref={box}>
      <svg className="search-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
      <input
        value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} onFocus={() => hits.length && setOpen(true)}
        placeholder="종목명 또는 티커 (예: NVDA, Apple, SPY)" aria-label="종목 검색" autoFocus={autoFocus}
        role="combobox" aria-expanded={open && hits.length > 0} aria-controls={listId} aria-autocomplete="list"
        aria-activedescendant={open && hits[active] ? `${listId}-${active}` : undefined} autoComplete="off" spellCheck={false}
      />
      {busy && <span className="spinner small" aria-label="검색 중" />}
      {open && hits.length > 0 && (
        <ul className="search-list" id={listId} role="listbox">
          {hits.map((h, i) => (
            <li key={h.symbol} id={`${listId}-${i}`} role="option" aria-selected={i === active}
                className={i === active ? "active" : ""} onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); pick(h.symbol); }}>
              <span className="sym">{h.symbol}</span>
              <span className="nm">{h.name}</span>
              <span className="ex">{h.type === "ETF" ? "ETF" : h.type === "CRYPTOCURRENCY" ? "코인" : h.exchange}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
