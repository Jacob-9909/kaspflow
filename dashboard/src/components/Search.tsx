/** 종목 검색 (자동완성). 입력이 멈추고 250ms 뒤 조회한다. 방향키·엔터로 고르고, 비어 있으면 최근 종목을 보여 준다. */
import { useEffect, useId, useRef, useState } from "react";
import { api, type SearchHit } from "../api";

const RECENT_KEY = "kf-recent";
/* 자동 포커스는 마우스·트랙패드 화면에서만 (모바일은 키보드가 바로 올라와 화면을 가린다) */
const finePointer = () => typeof matchMedia === "function" && matchMedia("(pointer: fine)").matches;

export function recentSymbols(): SearchHit[] {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]") as SearchHit[];
  } catch {
    return [];
  }
}

export function rememberSymbol(hit: SearchHit) {
  try {
    const list = [hit, ...recentSymbols().filter((x) => x.symbol !== hit.symbol)].slice(0, 8);
    localStorage.setItem(RECENT_KEY, JSON.stringify(list));
  } catch { /* 저장 못 해도 검색은 된다 */ }
}

export default function Search({ onPick, big = false, autoFocus = false }: { onPick: (symbol: string) => void; big?: boolean; autoFocus?: boolean }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [busy, setBusy] = useState(false);
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);
  const [auto] = useState(() => autoFocus && finePointer());
  // 페이지를 열자마자 자동 포커스로 최근 목록이 펼쳐져 아래 내용을 가리지 않게, 첫 포커스는 목록을 열지 않는다
  const skipFocusOpen = useRef(auto);
  const showingRecent = !q.trim();
  const items = showingRecent ? recentSymbols() : hits;

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

  const pick = (hit: SearchHit) => {
    setOpen(false);
    setQ("");
    rememberSymbol(hit);
    onPick(hit.symbol.toUpperCase());
  };

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, items.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Escape") setOpen(false);
    else if (e.key === "Enter") {
      e.preventDefault();
      if (open && items[active]) pick(items[active]);
      else if (/^[A-Za-z0-9.\-^=]{1,15}$/.test(q.trim())) pick({ symbol: q.trim().toUpperCase(), name: q.trim().toUpperCase(), type: "", exchange: "" });
    }
  };

  return (
    <div className={`search${big ? " big" : ""}`} ref={box}>
      <svg className="search-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
      <input
        value={q} onChange={(e) => { setQ(e.target.value); setActive(0); }} onKeyDown={onKey}
        onFocus={() => { if (skipFocusOpen.current) { skipFocusOpen.current = false; return; } setActive(0); setOpen(true); }}
        onClick={() => setOpen(true)}
        placeholder="종목명 또는 티커 (예: NVDA, Apple, SPY)" aria-label="종목 검색" autoFocus={auto}
        role="combobox" aria-expanded={open && items.length > 0} aria-controls={listId} aria-autocomplete="list"
        aria-activedescendant={open && items[active] ? `${listId}-${active}` : undefined} autoComplete="off" spellCheck={false}
      />
      {busy && <span className="spinner small" aria-label="검색 중" />}
      {open && items.length > 0 && (
        <ul className="search-list" id={listId} role="listbox" aria-label={showingRecent ? "최근 본 종목" : "검색 결과"}>
          {showingRecent && <li className="list-head" role="presentation">최근 본 종목</li>}
          {items.map((h, i) => (
            <li key={h.symbol} id={`${listId}-${i}`} role="option" aria-selected={i === active}
                className={i === active ? "active" : ""} onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); pick(h); }}>
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
