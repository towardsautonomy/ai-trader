import { num } from "@/lib/format";

interface Level {
  key: string;
  label: string;
  value: number;
  color: string;
}

const W = 640;
const H = 46;
const PAD = 24;

/**
 * Price ladder on one axis: initial stop, current stop, entry, last/exit, target.
 * Marker letters sit on the axis; exact values are in the table beside it, never colour alone.
 */
export function LevelsBar({ levels }: { levels: Level[] }) {
  const usable = levels.filter((l) => Number.isFinite(l.value) && l.value > 0);
  if (usable.length < 2) return null;
  const min = Math.min(...usable.map((l) => l.value));
  const max = Math.max(...usable.map((l) => l.value));
  const span = max - min || 1;
  const x = (v: number) => PAD + ((v - min) / span) * (W - 2 * PAD);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-[46px] w-full max-w-[640px]" role="img" aria-label={usable.map((l) => `${l.label} ${num(l.value)}`).join(", ")}>
      <line x1={PAD} x2={W - PAD} y1={30} y2={30} stroke="#2f4a3b" strokeWidth={1} />
      {usable.map((l, i) => (
        <g key={l.key}>
          <line x1={x(l.value)} x2={x(l.value)} y1={22} y2={38} stroke={l.color} strokeWidth={2} />
          <text x={x(l.value)} y={i % 2 === 0 ? 16 : 8} textAnchor="middle" fontSize={10} fill={l.color} fontFamily="var(--font-mono)">
            {l.key}
          </text>
        </g>
      ))}
    </svg>
  );
}
