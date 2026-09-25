/**
 * SymbolSelector: 심볼 선택 버튼 그룹
 * ============================================================
 * 사용 가능한 심볼(symbols)을 버튼으로 나열하고,
 * 선택된 심볼(selected)을 강조한다. 클릭하면 부모에 알린다(onSelect).
 */
interface Props {
  symbols: string[];
  selected: string | null;
  onSelect: (symbol: string) => void;
}

export function SymbolSelector({ symbols, selected, onSelect }: Props) {
  if (symbols.length === 0) {
    return <p className="muted">아직 수집된 심볼이 없습니다.</p>;
  }

  return (
    <div className="symbol-selector">
      {symbols.map((s) => (
        <button
          key={s}
          className={s === selected ? "chip chip--active" : "chip"}
          onClick={() => onSelect(s)}
        >
          {s}
        </button>
      ))}
    </div>
  );
}
