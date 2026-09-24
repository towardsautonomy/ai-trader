import { meterBar } from "@/lib/format";

interface MeterProps {
  label: string;
  /** How much of the limit is used, 0..1. */
  ratio: number;
  text: string;
}

/** `DAY LOSS [###-------] -0.6% / -2.0%`: goes amber at 60% of a limit, red at 85%. */
export function Meter({ label, ratio, text }: MeterProps) {
  const tone = ratio >= 0.85 ? "text-danger" : ratio >= 0.6 ? "text-amber" : "text-phos";
  return (
    <div className="flex items-baseline gap-2 whitespace-nowrap text-xs">
      <span className="text-dim">{label}</span>
      <span className={tone} aria-hidden>
        {meterBar(ratio)}
      </span>
      <span className="text-fg">{text}</span>
    </div>
  );
}
