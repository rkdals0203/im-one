import { useMemo, useState } from "react";

const ROW_HEIGHT = 44;
const VIEWPORT_HEIGHT = 352;
const OVERSCAN = 4;

export function VirtualTable({ columns, rows }: { columns: string[]; rows: Record<string, unknown>[] }) {
  const [scrollTop, setScrollTop] = useState(0);
  const range = useMemo(() => {
    const start = Math.max(Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN, 0);
    const visible = Math.ceil(VIEWPORT_HEIGHT / ROW_HEIGHT) + OVERSCAN * 2;
    return { start, end: Math.min(start + visible, rows.length) };
  }, [rows.length, scrollTop]);

  return (
    <div className="result-table-shell">
      <div className="result-table-head" style={{ gridTemplateColumns: `repeat(${Math.max(columns.length, 1)}, minmax(150px, 1fr))` }}>
        {columns.map((column) => <div key={column}>{column}</div>)}
      </div>
      <div className="result-table-scroll" onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}>
        <div className="virtual-spacer" style={{ height: rows.length * ROW_HEIGHT }}>
          {rows.slice(range.start, range.end).map((row, relativeIndex) => {
            const index = range.start + relativeIndex;
            return (
              <div
                className="result-table-row"
                key={index}
                style={{
                  top: index * ROW_HEIGHT,
                  height: ROW_HEIGHT,
                  gridTemplateColumns: `repeat(${Math.max(columns.length, 1)}, minmax(150px, 1fr))`,
                }}
              >
                {columns.map((column) => <div key={column} title={String(row[column] ?? "")}>{formatCell(row[column])}</div>)}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function formatCell(value: unknown): string {
  if (typeof value === "number") return new Intl.NumberFormat("ko-KR").format(value);
  if (value === null || value === undefined) return "-";
  return String(value);
}
