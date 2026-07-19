import {
  Bot,
  Calendar,
  CheckCircle2,
  CircleAlert,
  FileText,
  ListTodo,
  MessagesSquare,
  Plug,
  Settings2,
  ShieldCheck,
  UserRound,
  Wrench,
  type LucideIcon,
} from "lucide-react";
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

// --- readability (Module 15a) ------------------------------------------------
// The log is scanned far more often than it is read line by line, so each row
// carries two cheap visual keys: an icon for WHAT happened and an accent for WHERE
// it came from. Both are views over the raw event — no backend change, and the
// mono `event_type` stays on the row as the authoritative value.

// Accent per source system. Keyed off the palette tokens so the same source reads
// consistently in light and dark.
const SOURCE_ACCENT: Record<string, string> = {
  user: "bg-primary",
  chat: "bg-info",
  automation: "bg-success",
  mcp: "bg-warning",
  webhook: "bg-destructive",
  system: "bg-muted-foreground",
};

export function sourceAccent(source: string | null | undefined): string {
  if (!source) return "bg-muted-foreground/40";
  return SOURCE_ACCENT[source] ?? "bg-muted-foreground/40";
}

// Icon by event-type prefix (the `entity` half of `entity.verb`), with a few exact
// matches where the verb carries the meaning (an approval vs. a plain action).
const ICON_BY_TYPE: Record<string, LucideIcon> = {
  "action.queued": ShieldCheck,
  "action.approved": CheckCircle2,
  "action.rejected": CircleAlert,
};

const ICON_BY_PREFIX: Record<string, LucideIcon> = {
  action: ShieldCheck,
  tool: Wrench,
  chat: MessagesSquare,
  task: ListTodo,
  document: FileText,
  automation: Bot,
  run: Bot,
  lead: UserRound,
  client: UserRound,
  applicant: UserRound,
  resource: UserRound,
  caregiver: UserRound,
  schedule: Calendar,
  visit: Calendar,
  connector: Plug,
  webhook: Plug,
  settings: Settings2,
};

export function eventIcon(eventType: string): LucideIcon {
  const exact = ICON_BY_TYPE[eventType];
  if (exact) return exact;
  const prefix = eventType.split(".")[0] ?? "";
  return ICON_BY_PREFIX[prefix] ?? CircleAlert;
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
