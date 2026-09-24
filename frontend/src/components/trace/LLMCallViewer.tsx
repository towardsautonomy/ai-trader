"use client";

import { useState } from "react";
import { JsonBlock, TextBlock } from "@/components/ui/JsonBlock";
import { ErrorState, Loading } from "@/components/ui/States";
import { useLLMCall } from "@/hooks/queries";
import { num, usd } from "@/lib/format";

function tryParse(text: string): { ok: true; value: unknown } | { ok: false } {
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false };
  }
}

function Payload({ label, text }: { label: string; text: string }) {
  const parsed = tryParse(text);
  const [pretty, setPretty] = useState(false);
  return (
    <div>
      <div className="mb-1 flex items-center gap-3">
        <span className="label">{label}</span>
        <span className="text-2xs text-faint">{text.length.toLocaleString("en-US")} chars</span>
        {parsed.ok ? (
          <button type="button" className="text-2xs uppercase tracking-wider text-cyan hover:underline" onClick={() => setPretty((v) => !v)}>
            {pretty ? "show verbatim" : "format as json"}
          </button>
        ) : null}
      </div>
      {text === "" ? <div className="text-sm text-faint">(empty)</div> : pretty && parsed.ok ? <JsonBlock value={parsed.value} maxHeight="max-h-[480px]" /> : <TextBlock text={text} maxHeight="max-h-[480px]" />}
    </div>
  );
}

/** Lazily fetches the exact prompt and raw reply for one LLM call. Verbatim by default. */
export function LLMCallViewer({ callId }: { callId: number }) {
  const [open, setOpen] = useState(false);
  const call = useLLMCall(callId, open);

  return (
    <div>
      <button type="button" className="text-xs text-cyan hover:underline" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? "- hide" : "+ view"} exact prompt/response <span className="text-faint">(call #{callId})</span>
      </button>
      {open ? (
        <div className="mt-2 space-y-2 border-l-2 border-cyan/40 pl-3">
          {call.data ? (
            <>
              <div className="text-xs text-dim">
                {call.data.agent} via {call.data.model} | {num(call.data.latency_ms, 0)} ms | {call.data.tokens_in} in / {call.data.tokens_out} out | {usd(call.data.cost_usd, 4)}
              </div>
              {call.data.error ? <div className="text-sm text-danger">error: {call.data.error}</div> : null}
              <Payload label="prompt (exactly as sent)" text={call.data.prompt} />
              <Payload label="response (raw)" text={call.data.response} />
            </>
          ) : call.error ? (
            <ErrorState error={call.error} what={`llm call #${callId}`} />
          ) : (
            <Loading what={`llm call #${callId}`} />
          )}
        </div>
      ) : null}
    </div>
  );
}
