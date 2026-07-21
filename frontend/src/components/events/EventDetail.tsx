import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { eventDisplay } from "@/lib/events";
import { htmlToText } from "@/lib/text";

// The expanded view of one event, shared by the Event Log and every entity
// timeline. Best-effort by design: event payloads are deliberately heterogeneous
// (each writer decides its own shape), so this reads what it recognizes, shows
// the rest generically, and can never crash on a shape it has not seen.
//
// Three bands, in the order a human wants them:
//   1. the long text (a call transcript, an email body) — the thing you opened it for
//   2. a small grid of the recurring scalar fields, labeled in plain language
//   3. the raw JSON, one click further down — CLAUDE.md keeps technical detail
//      available, it just should not be the first thing in your face
//
// Presentational only: takes an event, fetches nothing.

const LONG_TEXT_CHARS = 80;

// payload/detail keys that get a human label in the grid. Anything else under
// `detail` still renders, with its key humanized.
const KNOWN_LABELS: Record<string, string> = {
  direction: "Direction",
  completed_at: "Occurred",
  occurred_at: "Occurred",
  scheduled_at: "Scheduled",
  activity_type: "Type",
  automation_name: "Automation",
  resolution: "Resolution",
  source: "Source",
  stage_name: "CRM stage",
};

// Keys that are plumbing, not information: ids, and anything already shown as
// the title, the long-text block, or the stage row.
const HIDDEN_KEYS = new Set([
  "summary",
  "notes",
  "error",
  "detail",
  "from",
  "to",
  "fields",
  "wh_activity_id",
  "external_id",
  "automation_id",
  "run_id",
  "lead_id",
  "id",
]);

function humanize(key: string): string {
  const words = key.replace(/[._]/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function formatValue(key: string, value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return String(value);
  if (Array.isArray(value)) {
    const parts = value.filter((v) => typeof v === "string" || typeof v === "number");
    return parts.length ? parts.join(", ") : null;
  }
  if (typeof value !== "string") return null;

  // Timestamps read as dates; everything else is already human text.
  if (/_at$/.test(key) || key === "occurred") {
    const d = new Date(value);
    if (!Number.isNaN(d.getTime())) {
      return d.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    }
  }
  return value;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="w-28 shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 flex-1 break-words">{value}</dd>
    </div>
  );
}

export function EventDetail({
  event,
  className,
}: {
  event: { event_type: string; payload?: Record<string, unknown> | null };
  className?: string;
}) {
  const [showRaw, setShowRaw] = useState(false);
  const payload = asRecord(event.payload) ?? {};
  const detail = asRecord(payload.detail) ?? {};

  // 1. Long text. The derived body covers CRM activities; the generic branches
  //    cover anything else that carries prose or an error string.
  const derived = eventDisplay(event);
  let longText = derived.body;
  if (!longText) {
    const notes = detail.notes ?? payload.notes;
    if (typeof notes === "string" && notes.trim()) longText = htmlToText(notes);
  }
  if (!longText && typeof payload.error === "string" && payload.error.length > LONG_TEXT_CHARS) {
    longText = payload.error;
  }

  // 2. Known + generic scalar rows.
  const rows: Array<{ label: string; value: string }> = [];

  // Stage keys are humanized generically, NOT looked up in a vertical's stage
  // config: this component renders leads, caregivers, clients and anything a
  // future vertical adds, and core must not know one vocabulary's labels.
  const from = payload.from;
  const to = payload.to;
  if (typeof from === "string" || typeof to === "string") {
    const fromLabel = typeof from === "string" ? humanize(from) : "—";
    const toLabel = typeof to === "string" ? humanize(to) : "—";
    rows.push({ label: "Stage", value: `${fromLabel} → ${toLabel}` });
  }

  const fields = payload.fields;
  if (Array.isArray(fields) && fields.length) {
    const named = fields.filter((f) => typeof f === "string").map((f) => humanize(f as string));
    if (named.length) rows.push({ label: "Fields changed", value: named.join(", ") });
  }

  for (const [key, value] of [...Object.entries(detail), ...Object.entries(payload)]) {
    if (HIDDEN_KEYS.has(key)) continue;
    if (rows.some((r) => r.label === (KNOWN_LABELS[key] ?? humanize(key)))) continue;
    const formatted = formatValue(key, value);
    if (formatted === null) continue;
    rows.push({ label: KNOWN_LABELS[key] ?? humanize(key), value: formatted });
  }

  const hasPayload = Object.keys(payload).length > 0;

  return (
    <div className={cn("space-y-3 text-xs", className)}>
      {longText && (
        <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground">
          {longText}
        </p>
      )}

      {rows.length > 0 && (
        <dl className="space-y-1 text-muted-foreground">
          {rows.map((r) => (
            <Row key={r.label} label={r.label} value={r.value} />
          ))}
        </dl>
      )}

      {hasPayload && (
        <div>
          <button
            onClick={() => setShowRaw((v) => !v)}
            className="inline-flex items-center gap-1 text-muted-foreground transition-colors hover:text-foreground"
          >
            {showRaw ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            Technical detail
          </button>
          {showRaw && (
            <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-3 text-xs text-muted-foreground">
              {JSON.stringify(event.payload, null, 2)}
            </pre>
          )}
        </div>
      )}

      {!longText && rows.length === 0 && !hasPayload && (
        <p className="text-muted-foreground">No further detail recorded.</p>
      )}
    </div>
  );
}
