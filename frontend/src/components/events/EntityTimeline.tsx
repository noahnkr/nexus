import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api, type EventOut } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { relativeTime } from "@/lib/utils";

// A compact, entity-scoped event feed — the timeline on a profile page. Generic on
// purpose (entityType/entityId props, no lead-specific copy) so M10's caregiver
// profile reuses it verbatim. Keyset "Load more" via the events API's cursor.
const PAGE_SIZE = 20;

function isError(ev: EventOut): boolean {
  return (
    ev.event_type.endsWith(".failed") ||
    (typeof ev.payload?.error === "string" && ev.payload.error.length > 0)
  );
}

function TimelineRow({ event }: { event: EventOut }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="border-b last:border-b-0">
      <div className="flex items-start gap-3 px-3 py-2.5">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-0.5 shrink-0 text-muted-foreground hover:text-foreground"
          aria-label={expanded ? "Collapse details" : "Expand details"}
        >
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
        </button>
        <div className="min-w-0 flex-1">
          <p className={cn("text-sm", isError(event) ? "text-destructive" : "text-foreground")}>
            {event.summary}
          </p>
          <div className="mt-1 flex items-center gap-2">
            <Badge variant="outline" className="shrink-0 text-[10px]">
              {event.source_system}
            </Badge>
            <span className="font-mono text-[11px] text-muted-foreground">
              {event.event_type}
            </span>
            <span className="text-[11px] text-muted-foreground">
              · {relativeTime(event.created_at)}
            </span>
          </div>
        </div>
      </div>
      {expanded && (
        <pre className="mx-3 mb-2.5 overflow-x-auto rounded-md bg-muted p-2.5 text-xs text-muted-foreground">
          {JSON.stringify(event.payload, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function EntityTimeline({
  entityType,
  entityId,
  refreshKey = 0,
}: {
  entityType: string;
  entityId: string;
  // Bumped by the parent after a write so the timeline refetches (a stage move or
  // field edit should show up immediately without a manual reload).
  refreshKey?: number;
}) {
  const [events, setEvents] = useState<EventOut[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  const loadFirst = useCallback(async () => {
    const page = await api.listEvents({
      entity_type: entityType,
      entity_id: entityId,
      limit: PAGE_SIZE,
    });
    setEvents(page.events);
    setCursor(page.next_cursor);
  }, [entityType, entityId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadFirst()
      .catch(() => {})
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [loadFirst, refreshKey]);

  const loadMore = async () => {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await api.listEvents({
        entity_type: entityType,
        entity_id: entityId,
        limit: PAGE_SIZE,
        cursor,
      });
      setEvents((prev) => {
        const seen = new Set(prev.map((e) => e.id));
        return [...prev, ...page.events.filter((e) => !seen.has(e.id))];
      });
      setCursor(page.next_cursor);
    } finally {
      setLoadingMore(false);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col gap-2 p-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }

  if (events.length === 0) {
    return (
      <p className="px-3 py-6 text-center text-sm text-muted-foreground">
        No activity recorded yet.
      </p>
    );
  }

  return (
    <div>
      {events.map((ev) => (
        <TimelineRow key={ev.id} event={ev} />
      ))}
      {cursor && (
        <div className="flex justify-center p-3">
          <Button variant="outline" size="sm" onClick={loadMore} disabled={loadingMore}>
            {loadingMore ? "Loading…" : "Load more"}
          </Button>
        </div>
      )}
    </div>
  );
}
