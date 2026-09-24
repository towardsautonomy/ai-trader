"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { EventStreamProvider } from "@/hooks/useEventStream";
import { NoticeProvider } from "@/hooks/useNotices";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Polling is the retry: a failed poll must surface as an error immediately, not after backoff.
            retry: false,
            refetchOnWindowFocus: true,
            refetchIntervalInBackground: true,
            staleTime: 1000,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <NoticeProvider>
        <EventStreamProvider>{children}</EventStreamProvider>
      </NoticeProvider>
    </QueryClientProvider>
  );
}
