import { num } from "@/lib/format";

const W = 320;
const H = 56;
const PAD = 4;

/** Recent closes the agents were shown. Green when the window closed up, red when down. */
export function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <div className="text-sm text-faint">not enough closes to draw</div>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const first = values[0] ?? 0;
  const last = values[values.length - 1] ?? 0;
  const pts = values.map((v, i) => {
    const x = PAD + (i / (values.length - 1)) * (W - 2 * PAD);
    const y = PAD + (1 - (v - min) / span) * (H - 2 * PAD);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const lastPt = pts[pts.length - 1]?.split(",") ?? ["0", "0"];
  const stroke = last >= first ? "#3dff8b" : "#ff4d4d";
  const change = first ? ((last - first) / first) * 100 : 0;

  return (
    <div className="flex items-center gap-3">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="shrink-0 border border-line bg-bg" role="img" aria-label={`Recent closes from ${num(first)} to ${num(last)}`}>
        <polyline points={pts.join(" ")} fill="none" stroke={stroke} strokeWidth={1.5} strokeLinejoin="round" />
        <circle cx={lastPt[0]} cy={lastPt[1]} r={2.5} fill={stroke} />
      </svg>
      <div className="grid grid-cols-[auto_auto] gap-x-3 text-xs">
        <span className="text-dim">bars</span>
        <span className="text-right">{values.length}</span>
        <span className="text-dim">high</span>
        <span className="text-right">{num(max)}</span>
        <span className="text-dim">low</span>
        <span className="text-right">{num(min)}</span>
        <span className="text-dim">change</span>
        <span className={`text-right ${change >= 0 ? "text-phos" : "text-danger"}`}>
          {change >= 0 ? "+" : "-"}
          {num(Math.abs(change))}%
        </span>
      </div>
    </div>
  );
}
