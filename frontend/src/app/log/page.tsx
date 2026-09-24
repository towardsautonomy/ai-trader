"use client";

import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { EventRow } from "@/components/EventRow";
import { OrdersTable } from "@/components/OrdersTable";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/ui/Panel";
import { Empty, QueryBody } from "@/components/ui/States";
import { useOrders } from "@/hooks/queries";
import { useDebounced } from "@/hooks/useDebounced";
import { api, errorText } from "@/lib/api";
import type { EventLevel, TraderEvent } from "@/lib/types";

const PAGE = 500;
const TAIL_LIMIT = 1000;
const LEVELS: ("all" | EventLevel)[] = ["all", "info", "warn", "error", "critical"];
/** Prefixes of the event kinds the backend emits; sent as `kind=` (server-side prefix match). */
const KINDS = ["", "decision.", "plan.", "risk.", "order.", "position.", "exit.", "review.", "lesson.", "killswitch.", "cycle.", "scan.", "engine.", "data."] as const;

/**
 * History pages backwards through the whole table: each page asks the server
 * for the newest PAGE events with `before_id=<oldest id loaded>`. Level, kind
 * prefix and message search are all applied by the server, so they cover all
 * history rather than just the loaded rows. A separate tail query polls
 * `after_id=<newest id of the first page>` so new events appear without
 * refetching history.
 */
function EventLog() {
  const qc = useQueryClient();
  const [level, setLevel] = useState<"all" | EventLevel>("all");
  const [kind, setKind] = useState("");
  const [search, setSearch] = useState("");
  const [follow, setFollow] = useState(true);
  const q = useDebounced(search.trim());
  const filters = { level: level === "all" ? undefined : level, kind: kind || undefined, q: q || undefined };

  const history = useInfiniteQuery({
    queryKey: ["log", "history", level, kind, q],
    queryFn: ({ pageParam }) => api.events({ ...filters, limit: PAGE, before_id: pageParam }),
    initialPageParam: undefined as number | undefined,
    // pages arrive oldest-first, so the first row of the last page is the oldest event loaded
    getNextPageParam: (lastPage) => (lastPage.length < PAGE ? undefined : lastPage[0]?.id),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const firstPage = history.data?.pages[0];
  const newestId = firstPage?.[firstPage.length - 1]?.id ?? 0;

  const tail = useQuery({
    queryKey: ["log", "tail", level, kind, q, newestId],
    queryFn: () => api.events({ ...filters, after_id: newestId, limit: TAIL_LIMIT }),
    enabled: history.data !== undefined && follow,
    refetchInterval: 5000,
  });

  const rows = useMemo(() => {
    const newestFirst: TraderEvent[] = [...(tail.data ?? [])].reverse();
    for (const page of history.data?.pages ?? []) newestFirst.push(...[...page].reverse());
    return newestFirst;
  }, [history.data, tail.data]);

  const newest = rows[0]?.id;
  const oldest = rows[rows.length - 1]?.id;
  const tailFull = (tail.data?.length ?? 0) >= TAIL_LIMIT;
  const filtered = level !== "all" || kind !== "" || q !== "";

  return (
    <Panel
      title="EVENT HISTORY"
      right={
        <>
          <span>
            {rows.length} loaded{newest !== undefined ? `, #${newest} back to #${oldest}` : ""}
            {filtered ? ", filtered by server" : ""}
          </span>
          <button type="button" aria-pressed={follow} onClick={() => setFollow((v) => !v)} className={`border px-1 ${follow ? "border-phos text-phos" : "border-line text-dim"}`}>
            {follow ? "FOLLOWING" : "FOLLOW OFF"}
          </button>
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-2 py-1.5">
        <input
          className="input w-72"
          placeholder="search all history by message text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search event messages"
        />
        <label className="flex items-center gap-1.5 text-xs text-dim">
          kind
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Filter by event kind prefix">
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {k === "" ? "all kinds" : `${k}*`}
              </option>
            ))}
          </select>
        </label>
        <span className="flex gap-1 text-xs">
          {LEVELS.map((lv) => (
            <button
              key={lv}
              type="button"
              aria-pressed={level === lv}
              onClick={() => setLevel(lv)}
              className={`border px-1.5 py-0.5 uppercase ${level === lv ? "border-phos text-phos" : "border-line text-dim hover:text-fg"}`}
            >
              {lv}
            </button>
          ))}
        </span>
        <button type="button" className="btn" onClick={() => void qc.resetQueries({ queryKey: ["log"] })} disabled={history.isFetching}>
          {history.isFetching && !history.isFetchingNextPage ? "LOADING..." : "RELOAD"}
        </button>
        {search.trim() !== q ? <span className="text-xs text-dim">searching...</span> : null}
        {tailFull ? <span className="text-xs text-amber">more than {TAIL_LIMIT} new events since load: press RELOAD</span> : null}
        {tail.error ? <span className="text-xs text-danger">live tail failing; newest events may be missing</span> : null}
      </div>
      <QueryBody query={history} what="event history">
        {() => (
          <div>
            {rows.length === 0 ? (
              <Empty>{filtered ? "no events in the whole history match these filters" : "no events recorded"}</Empty>
            ) : (
              <div className="py-1">
                {rows.map((e) => (
                  <EventRow key={e.id} e={e} fullDate />
                ))}
              </div>
            )}
            <div className="flex flex-wrap items-center gap-3 border-t border-line px-2 py-1.5 text-xs text-dim">
              {history.hasNextPage ? (
                <button type="button" className="btn" disabled={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()}>
                  {history.isFetchingNextPage ? "LOADING..." : `LOAD ${PAGE} OLDER`}
                </button>
              ) : (
                <span>beginning of history reached</span>
              )}
              {history.isFetchNextPageError ? <span className="text-danger">loading older events failed: {errorText(history.error)}</span> : null}
              <span>search, kind and level are applied by the server across all history</span>
            </div>
          </div>
        )}
      </QueryBody>
    </Panel>
  );
}

function OrdersLog() {
  const orders = useOrders();
  return (
    <Panel title="ORDERS" right={orders.data ? <span>{orders.data.length} most recent, this mode</span> : null}>
      <QueryBody query={orders} what="orders">
        {(rows) => <OrdersTable orders={rows} showLinks />}
      </QueryBody>
    </Panel>
  );
}

export default function LogPage() {
  const [tab, setTab] = useState<"events" | "orders">("events");
  return (
    <div>
      <PageHeader
        title="log"
        right={
          <span className="flex gap-1 text-xs">
            {(["events", "orders"] as const).map((t) => (
              <button
                key={t}
                type="button"
                aria-pressed={tab === t}
                onClick={() => setTab(t)}
                className={`border px-2 py-0.5 uppercase tracking-wider ${tab === t ? "border-phos text-phos" : "border-line text-dim hover:text-fg"}`}
              >
                {t}
              </button>
            ))}
          </span>
        }
      />
      {tab === "events" ? <EventLog /> : <OrdersLog />}
    </div>
  );
}
