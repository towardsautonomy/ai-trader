import { apiUrl, errorText, isUnreachable } from "@/lib/api";

export function Loading({ what }: { what: string }) {
  return (
    <div className="px-3 py-4 text-sm text-dim">
      loading {what}
      <span className="animate-blink">_</span>
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="px-3 py-4 text-sm text-faint">{children}</div>;
}

/** Inline error for one panel. A dead backend is named as such, not shown as a generic failure. */
export function ErrorState({ error, what }: { error: unknown; what: string }) {
  if (isUnreachable(error)) {
    return (
      <div className="px-3 py-4 text-sm text-danger">
        <div className="font-bold tracking-wider">BACKEND UNREACHABLE</div>
        <div className="mt-1 text-dim">
          {what} unavailable: no answer from {apiUrl()}
        </div>
      </div>
    );
  }
  return (
    <div className="px-3 py-4 text-sm text-danger">
      <div className="font-bold tracking-wider">ERROR LOADING {what.toUpperCase()}</div>
      <div className="mt-1 break-words text-dim">{errorText(error)}</div>
    </div>
  );
}

interface QueryLike<T> {
  data: T | undefined;
  error: unknown;
  isPending: boolean;
}

/**
 * Standard loading / error / data switch for a polled query. Once data has
 * loaded it stays on screen through a failed refetch, with a stale marker,
 * rather than blanking a panel the owner is reading.
 */
export function QueryBody<T>({ query, what, children }: { query: QueryLike<T>; what: string; children: (data: T) => React.ReactNode }) {
  if (query.data === undefined) {
    if (query.isPending && !query.error) return <Loading what={what} />;
    return <ErrorState error={query.error} what={what} />;
  }
  return (
    <>
      {query.error ? (
        <div className="border-b border-danger/50 bg-danger/10 px-2 py-0.5 text-2xs uppercase tracking-wider text-danger">
          stale: last refresh failed ({isUnreachable(query.error) ? "backend unreachable" : errorText(query.error)})
        </div>
      ) : null}
      {children(query.data)}
    </>
  );
}
