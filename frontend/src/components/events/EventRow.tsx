import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
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

  return (
    <div className="border-b last:border-b-0">
      <div className="flex items-start gap-3 px-4 py-3">
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

        <Badge variant="outline" className="mt-0.5 shrink-0">
          {event.source_system}
        </Badge>

        <div className="min-w-0 flex-1">
          <p
            className={cn(
              "text-sm",
              error ? "text-destructive" : "text-foreground",
            )}
          >
            {event.summary}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-muted-foreground">
              {event.event_type}
            </span>
            {hasEntity && (
              <button
                onClick={() =>
                  onEntityClick(event.entity_type!, event.entity_id!)
                }
                className="rounded-full border border-input px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
                title={`Show everything for this ${event.entity_type}`}
              >
                {event.entity_type} ·{" "}
                <span className="font-mono">
                  {event.entity_id!.slice(0, 8)}
                </span>
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
