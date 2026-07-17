import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Unpack an error thrown by api.ts's `json<T>` helper (message shape:
// "<status> <statusText>: <body>"). Returns the HTTP status and FastAPI's `detail`
// (parsed from JSON when possible) so callers can render plain 422/409 messages
// inline instead of the raw error string.
export function parseApiError(e: unknown): { status?: number; detail: unknown } {
  const msg = e instanceof Error ? e.message : String(e);
  const m = msg.match(/^(\d{3})\b[^:]*:\s([\s\S]*)$/);
  const status = m ? Number(m[1]) : undefined;
  const bodyText = m ? m[2] : msg;
  try {
    const parsed = JSON.parse(bodyText);
    return { status, detail: parsed?.detail ?? parsed };
  } catch {
    return { status, detail: bodyText };
  }
}

// Compact "3m ago" / "2h ago" / "Jul 14" relative time for activity feeds.
export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const secs = Math.round((Date.now() - then) / 1000);
  if (secs < 45) return "just now";
  if (secs < 90) return "1m ago";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}
