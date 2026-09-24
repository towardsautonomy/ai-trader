import { STANCE_GLYPH, agentAbbr, num, stanceClass } from "@/lib/format";
import type { StanceChip } from "@/lib/types";

/** `MOM ▲0.74  MR ▬0.45  VOL ▲0.66  SKP ▬0.60` */
export function StanceChips({ stances }: { stances: StanceChip[] }) {
  if (stances.length === 0) return <span className="text-faint">no opinions</span>;
  return (
    <span className="inline-flex flex-wrap gap-x-3 whitespace-nowrap">
      {stances.map((s) => (
        <span key={s.agent} title={`${s.agent}: ${s.stance} ${num(s.confidence)}`}>
          <span className="text-dim">{agentAbbr(s.agent)}</span>{" "}
          <span className={stanceClass(s.stance)}>
            {STANCE_GLYPH[s.stance] ?? "?"}
            {num(s.confidence)}
          </span>
        </span>
      ))}
    </span>
  );
}
