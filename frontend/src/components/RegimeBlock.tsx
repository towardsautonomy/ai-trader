import { KV } from "@/components/ui/KV";
import { DASH, num, upper } from "@/lib/format";
import type { Regime } from "@/lib/types";

function biasClass(bias: string | undefined): string {
  const b = (bias ?? "").toLowerCase();
  if (b.includes("bull") || b === "long" || b === "risk_on") return "text-phos";
  if (b.includes("bear") || b === "short" || b === "risk_off") return "text-danger";
  return "text-fg";
}

export function RegimeBlock({ regime }: { regime: Partial<Regime> | null }) {
  if (!regime || Object.keys(regime).length === 0) return <div className="text-sm text-faint">no regime read yet</div>;
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-3 gap-3">
        <KV k="regime">
          <span className="font-bold text-cyan">{upper(regime.label) || DASH}</span>
        </KV>
        <KV k="bias">
          <span className={`font-bold ${biasClass(regime.bias)}`}>{upper(regime.bias) || DASH}</span>
        </KV>
        <KV k="confidence">{num(regime.confidence)}</KV>
      </div>
      <KV k="summary">{regime.summary || DASH}</KV>
      <KV k="what works">{regime.what_works || DASH}</KV>
    </div>
  );
}
