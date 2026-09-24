"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { apiUrl, isUnreachable } from "@/lib/api";
import { useStatus } from "@/hooks/queries";
import { useEventStream, type ConnState } from "@/hooks/useEventStream";
import { KillSwitch } from "./KillSwitch";

const NAV = [
  { href: "/", label: "TERMINAL" },
  { href: "/history", label: "HISTORY" },
  { href: "/agents", label: "AGENTS" },
  { href: "/log", label: "LOG" },
  { href: "/setup", label: "SETUP" },
] as const;

const CONN: Record<ConnState, { text: string; tone: string }> = {
  live: { text: "WS LIVE", tone: "text-phos" },
  connecting: { text: "WS CONNECTING", tone: "text-amber" },
  down: { text: "WS DOWN", tone: "text-danger" },
};

function ModeBadge() {
  const status = useStatus();
  const mode = status.data?.mode;
  if (!mode) return <span className="border border-line px-2 py-0.5 text-xs text-dim">MODE ?</span>;
  if (status.data?.demo) {
    return (
      <Link href="/setup" className="border border-amber px-2 py-0.5 text-xs font-bold tracking-[0.2em] text-amber" title="synthetic market, clock ignored: nothing is real">
        DEMO
      </Link>
    );
  }
  if (mode === "live") {
    return <span className="border border-danger bg-danger px-2 py-0.5 text-xs font-bold tracking-[0.2em] text-bg">LIVE: REAL MONEY</span>;
  }
  return (
    <Link href="/setup" className="border border-phos px-2 py-0.5 text-xs font-bold tracking-[0.2em] text-phos" title="simulated fills; switch modes on SETUP">
      PAPER
    </Link>
  );
}

/** Always visible: the way to real money (or back). It opens the guarded switch on SETUP. */
function ModeSwitchButton() {
  const s = useStatus().data;
  if (!s || s.demo) return null;
  return s.mode === "live" ? (
    <Link href="/setup" className="btn whitespace-nowrap">
      SWITCH TO PAPER
    </Link>
  ) : (
    <Link href="/setup" className="btn btn-danger whitespace-nowrap" title="opens the readiness checklist and the guarded switch">
      SWITCH TO LIVE
    </Link>
  );
}

export function Header({ scanlines, onToggleScanlines }: { scanlines: boolean; onToggleScanlines: () => void }) {
  const pathname = usePathname();
  const status = useStatus();
  const { conn, retries } = useEventStream();
  const live = status.data?.mode === "live";
  const down = isUnreachable(status.error);

  return (
    <header className={`border-b bg-bg ${live ? "border-danger border-t-4" : "border-line"}`}>
      <div className="flex items-center gap-4 px-3 py-1.5">
        <Link href="/" className="whitespace-nowrap text-sm font-bold tracking-wider text-phos">
          ai_trader://terminal<span className="animate-blink">_</span>
        </Link>
        <nav className="flex items-center gap-1 text-xs">
          {NAV.map((n) => {
            const active = n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                className={`border px-2 py-0.5 tracking-wider ${active ? "border-phos text-phos" : "border-transparent text-dim hover:text-fg"}`}
              >
                {n.label}
              </Link>
            );
          })}
        </nav>
        <ModeBadge />
        <ModeSwitchButton />
        <span className={`whitespace-nowrap text-xs ${down ? "font-bold text-danger" : "text-dim"}`} title={apiUrl()}>
          {down ? "API DOWN" : status.data ? "API OK" : "API ..."}
        </span>
        <span className={`whitespace-nowrap text-xs ${CONN[conn].tone}`}>
          {CONN[conn].text}
          {conn !== "live" && retries > 0 ? ` (retry ${retries})` : ""}
        </span>
        <button type="button" className="hidden whitespace-nowrap text-2xs uppercase tracking-wider text-faint hover:text-fg lg:inline" onClick={onToggleScanlines}>
          scanlines {scanlines ? "on" : "off"}
        </button>
        <div className="ml-auto">
          <KillSwitch />
        </div>
      </div>
    </header>
  );
}
