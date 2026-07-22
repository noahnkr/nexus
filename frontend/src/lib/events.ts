import {
  Bot,
  Calendar,
  CheckCircle2,
  CircleAlert,
  ClipboardCheck,
  FileText,
  ListTodo,
  Mail,
  MessageSquare,
  MessagesSquare,
  Phone,
  Plug,
  Settings2,
  ShieldCheck,
  StickyNote,
  UserRound,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import type { EventOut, EventQuery } from "@/lib/api";
import { htmlToText } from "@/lib/text";

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
  // Connector sources (Module 18). They share the `webhook` accent because to a
  // reader scanning the log they mean the same thing — "this came from outside" —
  // regardless of whether it was pushed to us or polled by us.
  welcomehome: "bg-destructive",
  goto: "bg-destructive",
  gmail: "bg-destructive",
  gcal: "bg-destructive",
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
  // Client and caregiver timelines carry these; without them the rows fell
  // through to the alert icon, which reads as "something went wrong".
  call: Phone,
  credential: ShieldCheck,
  communication: MessagesSquare,
  email: Mail,
  sms: MessageSquare,
};

// WelcomeHome activity type -> icon. CRM activities are the bulk of a lead's
// timeline, and they all share one event type (`lead.activity_logged`), so the
// icon has to come from the payload or every row looks identical.
const ICON_BY_ACTIVITY: Record<string, LucideIcon> = {
  Email: Mail,
  Call: Phone,
  Text: MessageSquare,
  Note: StickyNote,
  Assessment: ClipboardCheck,
};

export function eventIcon(
  eventType: string,
  payload?: Record<string, unknown> | null,
): LucideIcon {
  if (eventType.endsWith(".activity_logged")) {
    const activityType = activityDetail(payload)?.activity_type;
    if (typeof activityType === "string") {
      return ICON_BY_ACTIVITY[activityType] ?? FileText;
    }
  }
  const exact = ICON_BY_TYPE[eventType];
  if (exact) return exact;
  const prefix = eventType.split(".")[0] ?? "";
  return ICON_BY_PREFIX[prefix] ?? CircleAlert;
}

// --- display derivation (v1.1.3) ---------------------------------------------
// Stored summaries are immutable (CLAUDE.md), and the ones written before this
// version carry two defects baked in at write time: raw HTML tags, and a 120-char
// truncation (398 of 913 activity summaries sit exactly at the cap). Both are
// fixed HERE, at read time, by deriving from `detail` — which always holds the
// full, original text — rather than trusting the stored one-liner.

function activityDetail(
  payload: Record<string, unknown> | null | undefined,
): Record<string, unknown> | null {
  const detail = payload?.detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return null;
  return detail as Record<string, unknown>;
}

export interface EventDisplay {
  title: string; // the one-line heading for the row
  body: string | null; // full long-form text, plain, or null when there is none
}

/**
 * What a timeline row should actually show.
 *
 * For CRM activities the title is rebuilt client-side from the structured
 * fields ("Email (outbound)"), which sidesteps the stored summary's HTML and
 * truncation in one move; the body is the full notes converted to plain text.
 * Everything else keeps the server-derived summary and has no body.
 *
 * Never throws: unknown activity types, absent notes, and odd payload shapes
 * all degrade to a title with no body.
 */
export function eventDisplay(ev: {
  event_type: string;
  payload?: Record<string, unknown> | null;
}): EventDisplay {
  if (ev.event_type.endsWith(".activity_logged")) {
    const detail = activityDetail(ev.payload);
    const activityType = detail?.activity_type;
    if (typeof activityType === "string" && activityType.trim()) {
      const direction = detail?.direction;
      const title =
        typeof direction === "string" && direction.trim()
          ? `${activityType} (${direction})`
          : activityType;
      const notes = detail?.notes;
      const body = typeof notes === "string" ? htmlToText(notes) : "";
      return { title, body: body || null };
    }
  }
  return { title: fallbackSummary(ev), body: null };
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
