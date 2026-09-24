"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNotices } from "@/hooks/useNotices";
import { ApiError, api, errorText } from "@/lib/api";
import { instrumentLabel } from "@/lib/format";
import type { Position } from "@/lib/types";

/** Manual close with a two-click confirm. The backend only queues the close, so the outcome shows up in the event stream. */
export function CloseControl({ p }: { p: Position }) {
  const qc = useQueryClient();
  const { notify } = useNotices();
  const [confirming, setConfirming] = useState(false);
  const label = instrumentLabel(p.instrument, p.instrument_key);

  const close = useMutation({
    mutationFn: () => api.closePosition(p.id),
    onSuccess: (res) => {
      notify("ok", `CLOSE ACCEPTED for ${p.qty} ${label}: position is now ${res.status.toUpperCase()}. Watch the event stream for the fill.`);
      setConfirming(false);
      void qc.invalidateQueries({ queryKey: ["positions"] });
      void qc.invalidateQueries({ queryKey: ["position", p.id] });
    },
    onError: (e) => {
      // 409: no longer open (already closing/closed). 404: the backend does not know this id. Neither sent a sell order.
      const refused = e instanceof ApiError && (e.status === 409 || e.status === 404);
      notify("fail", `CLOSE ${label} ${refused ? "REFUSED, no order was sent" : "FAILED"}: ${errorText(e)}`);
      setConfirming(false);
      void qc.invalidateQueries({ queryKey: ["positions"] });
      void qc.invalidateQueries({ queryKey: ["position", p.id] });
    },
  });

  if (p.status === "closing") return <span className="text-xs text-amber">CLOSING...</span>;
  if (!confirming) {
    return (
      <button type="button" className="btn btn-danger" onClick={() => setConfirming(true)}>
        CLOSE
      </button>
    );
  }
  return (
    <span className="inline-flex items-center gap-1">
      <button type="button" className="btn btn-danger" disabled={close.isPending} onClick={() => close.mutate()}>
        {close.isPending ? "SENDING..." : `SELL ${p.qty}?`}
      </button>
      <button type="button" className="btn" disabled={close.isPending} onClick={() => setConfirming(false)}>
        NO
      </button>
    </span>
  );
}
