const PRE = "overflow-auto whitespace-pre-wrap break-words border border-line bg-bg p-2 text-xs text-fg";

/** Pretty-prints arbitrary JSON the backend recorded, for context the UI has no fixed schema for. */
export function JsonBlock({ value, maxHeight = "max-h-80" }: { value: unknown; maxHeight?: string }) {
  return <pre className={`${PRE} ${maxHeight}`}>{JSON.stringify(value, null, 2)}</pre>;
}

/** Verbatim text, byte for byte as recorded. */
export function TextBlock({ text, maxHeight = "max-h-96" }: { text: string; maxHeight?: string }) {
  return <pre className={`${PRE} ${maxHeight}`}>{text}</pre>;
}
