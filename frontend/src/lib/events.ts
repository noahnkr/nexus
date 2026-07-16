import type { EventOut, EventQuery } from "@/lib/api";

// The API is the source of truth for summaries (server-derived). Realtime INSERT
// payloads arrive without one, so we render a best-effort line until the next
// fetch replaces it: an explicit payload.summary if present, else a humanized
// event_type — mirroring only the generic fallback in event_summaries.py.
export function fallbackSummary(row: {
  event_type: string;
  payload?: Record<string, unknown> | null;
}): string {
  const s = row.payload?.summary;
  if (typeof s === "string" && s.trim()) return s.trim();
  const words = row.event_type.replace(/[._]/g, " ").trim();
  if (!words) return "Event";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// Does a (possibly live) event match the active filters? Mirrors the server-side
// WHERE clause so a live row is only prepended when it belongs in the view.
export function matchesFilters(ev: EventOut, f: EventQuery): boolean {
  if (f.source_system && ev.source_system !== f.source_system) return false;
  if (f.event_type && ev.event_type !== f.event_type) return false;
  if (f.entity_type && ev.entity_type !== f.entity_type) return false;
  if (f.entity_id && ev.entity_id !== f.entity_id) return false;
  if (f.since && new Date(ev.created_at) < new Date(f.since)) return false;
  if (f.until && new Date(ev.created_at) > new Date(f.until)) return false;
  return true;
}
