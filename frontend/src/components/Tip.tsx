import { useCallback, useState, type ReactNode } from "react";

/** Shared hover tooltip: const [tip, show, hide] = useTip(); render {tip} once. */
export function useTip(): [ReactNode, (e: { clientX: number; clientY: number }, content: ReactNode) => void, () => void] {
  const [s, setS] = useState<{ x: number; y: number; c: ReactNode } | null>(null);
  const show = useCallback((e: { clientX: number; clientY: number }, c: ReactNode) => setS({ x: e.clientX, y: e.clientY, c }), []);
  const hide = useCallback(() => setS(null), []);
  const node = s ? <div className="tooltip" style={{
    left: Math.min(s.x + 14, window.innerWidth - 350), top: Math.min(s.y + 14, window.innerHeight - 160),
  }}>{s.c}</div> : null;
  return [node, show, hide];
}

export function HazardLegend({ codes, colors, labels }: { codes: string[]; colors: Record<string, string>; labels: Record<string, string> }) {
  return <div className="row wrap" style={{ gap: 8 }}>
    {codes.map((c) => <span key={c} className="chip"><span className="swatch" style={{ background: colors[c] }} />{labels[c] ?? c}</span>)}
  </div>;
}
