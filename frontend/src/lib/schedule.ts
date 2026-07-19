// Schedule board UI helpers (Module 12b, vertical seam): status tones, week math,
// and time formatting. Types live in lib/api.ts (mirroring the 12a payloads); this
// file is the presentation layer the board/drawer/dialog share. Status tones come
// from the semantic tokens (--primary/--warning/--success/--destructive/--muted) so
// they read in both themes — no hex literals (the 6b convention).
import type { ScheduleVisit, VisitStatus } from "./api";

type BadgeVariant =
  | "default"
  | "secondary"
  | "destructive"
  | "outline"
  | "success"
  | "warning"
  | "info";

export interface StatusMeta {
  label: string;
  badge: BadgeVariant;
  block: string; // classes for a VisitBlock chip in the board grid
}

export const VISIT_STATUS_META: Record<VisitStatus, StatusMeta> = {
  scheduled: {
    label: "Scheduled",
    badge: "info",
    block: "border-primary/30 bg-primary/10 text-foreground hover:border-primary/50",
  },
  open: {
    label: "Open shift",
    badge: "warning",
    block: "border-warning/40 bg-warning/10 text-warning hover:border-warning/60",
  },
  called_out: {
    label: "Called out",
    badge: "secondary",
    block:
      "border-dashed border-muted-foreground/40 bg-muted text-muted-foreground line-through hover:border-muted-foreground/60",
  },
  completed: {
    label: "Completed",
    badge: "success",
    block: "border-success/30 bg-success/10 text-success hover:border-success/50",
  },
  cancelled: {
    label: "Cancelled",
    badge: "outline",
    block:
      "border-border bg-muted/40 text-muted-foreground line-through opacity-70 hover:opacity-100",
  },
  no_show: {
    label: "No-show",
    badge: "destructive",
    block:
      "border-destructive/30 bg-destructive/10 text-muted-foreground line-through hover:border-destructive/50",
  },
};

export function statusMeta(status: VisitStatus): StatusMeta {
  return VISIT_STATUS_META[status];
}

// --- Week math (Monday-start, all in the viewer's local clock) ----------------
function parseLocalDate(iso: string): Date {
  // "YYYY-MM-DD" as a LOCAL midnight (avoids the UTC-parse day shift `new Date("…")` does).
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function todayIso(): string {
  return isoDate(new Date());
}

// Monday of the week containing `input` (a YYYY-MM-DD string or a Date).
export function weekStartOf(input: string | Date): string {
  const d = typeof input === "string" ? parseLocalDate(input) : new Date(input);
  const dow = (d.getDay() + 6) % 7; // Mon=0 … Sun=6
  d.setDate(d.getDate() - dow);
  return isoDate(d);
}

export function addWeeks(weekStart: string, n: number): string {
  const d = parseLocalDate(weekStart);
  d.setDate(d.getDate() + n * 7);
  return isoDate(d);
}

export interface DayColumn {
  iso: string; // YYYY-MM-DD
  weekday: string; // "Mon"
  label: string; // "Jul 21"
  isToday: boolean;
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function dayColumns(weekStart: string): DayColumn[] {
  const start = parseLocalDate(weekStart);
  const today = todayIso();
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    return {
      iso: isoDate(d),
      weekday: WEEKDAYS[i],
      label: d.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
      isToday: isoDate(d) === today,
    };
  });
}

// The local calendar day a visit falls on — how a visit maps to a board column.
export function visitDayIso(v: ScheduleVisit): string {
  return isoDate(new Date(v.start_time));
}

// A human label for the whole week (e.g. "Jul 21 – 27, 2027").
export function weekLabel(weekStart: string): string {
  const start = parseLocalDate(weekStart);
  const end = new Date(start);
  end.setDate(start.getDate() + 6);
  const startStr = start.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const endStr = end.toLocaleDateString(undefined, {
    month: start.getMonth() === end.getMonth() ? undefined : "short",
    day: "numeric",
    year: "numeric",
  });
  return `${startStr} – ${endStr}`;
}

// --- Time formatting ----------------------------------------------------------
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatRange(v: { start_time: string; end_time: string }): string {
  return `${formatTime(v.start_time)}–${formatTime(v.end_time)}`;
}

export function formatDayTime(iso: string): string {
  const d = new Date(iso);
  const day = d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
  return `${day} · ${formatTime(iso)}`;
}

// Hours between two ISO timestamps, one decimal (for the create dialog preview).
export function hoursBetween(startIso: string, endIso: string): number {
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  return Math.round((ms / 3_600_000) * 10) / 10;
}
