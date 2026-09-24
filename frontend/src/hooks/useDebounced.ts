"use client";

import { useEffect, useState } from "react";

/** The value, once it has stopped changing for delayMs. Keeps a search box from firing a request per keystroke. */
export function useDebounced<T>(value: T, delayMs = 300): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return settled;
}
