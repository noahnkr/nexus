import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { eventIcon, sourceAccent } from "@/lib/events";
import { eventTypeLabel, sourceLabel } from "@/lib/recipe";
import type { EventOut } from "@/lib/api";

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function isErrorEvent(ev: EventOut): boolean {
  return (
    ev.event_type.endsWith(".failed") ||
    (typeof ev.payload?.error === "string" && ev.payload.error.length > 0) ||
    ev.payload?.is_error === true
  );
}

// One audit line. The plain-language summary leads; the type gets an icon and a
// readable label so the log can be scanned without decoding `entity.verb`, with
// the raw type demoted to mono secondary text (still the authoritative value).
// A left accent bar keys the row to its source system.
export function EventRow({
  event,
  onEntityClick,
}: {
  event: EventOut;
  onEntityClick: (entityType: string, entityId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasEntity = Boolean(event.entity_type && event.entity_id);
  const error = isErrorEvent(event);
  const Icon = eventIcon(event.event_type);

  return (
    <div className="relative border-b last:border-b-0">
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-0 left-0 w-0.5",
          error ? "bg-destructive" : sourceAccent(event.source_system),
        )}
      />
      <div className="flex items-start gap-3 py-3 pl-4 pr-4">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-0.5 shrink-0 text-muted-foreground hover:text-foreground"
          aria-label={expanded ? "Collapse details" : "Expand details"}
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </button>

        <time className="mt-0.5 w-36 shrink-0 font-mono text-xs text-muted-foreground">
          {formatTime(event.created_at)}
        </time>

        <Icon
          className={cn(
            "mt-0.5 h-4 w-4 shrink-0",
            error ? "text-destructive" : "text-muted-foreground",
          )}
          aria-hidden
        />

        <div className="min-w-0 flex-1">
          <p className={cn("text-sm", error ? "text-destructive" : "text-foreground")}>
            {event.summary}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <span className="font-medium">{eventTypeLabel(event.event_type)}</span>
            <span className="font-mono opacity-70">{event.event_type}</span>
            <span aria-hidden>·</span>
            <span>{sourceLabel(event.source_system)}</span>
            {hasEntity && (
              <button
                onClick={() => onEntityClick(event.entity_type!, event.entity_id!)}
                className="rounded-full border border-input px-2 py-0.5 transition-colors hover:bg-accent hover:text-accent-foreground"
                title={`Show everything for this ${event.entity_type}`}
              >
                {event.entity_type} ·{" "}
                <span className="font-mono">{event.entity_id!.slice(0, 8)}</span>
              </button>
            )}
          </div>
        </div>
      </div>

      {expanded && (
        <pre className="mx-4 mb-3 overflow-x-auto rounded-md bg-muted p-3 text-xs text-muted-foreground">
          {JSON.stringify(event.payload, null, 2)}
        </pre>
      )}
    </div>
  );
}
