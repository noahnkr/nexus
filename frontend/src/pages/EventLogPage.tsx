import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { api, type EventFacets, type EventOut, type EventQuery } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { fallbackSummary, matchesFilters } from "@/lib/events";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EventFilters } from "@/components/events/EventFilters";
import { EventRow } from "@/components/events/EventRow";

const FILTER_KEYS = [
  "source_system",
  "event_type",
  "entity_type",
  "entity_id",
  "since",
  "until",
] as const;

const PAGE_SIZE = 50;

function paramsToFilters(sp: URLSearchParams): EventQuery {
  const f: EventQuery = {};
  for (const k of FILTER_KEYS) {
    const v = sp.get(k);
    if (v) f[k] = v;
  }
  return f;
}

export function EventLogPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = paramsToFilters(searchParams);
  const filtersKey = JSON.stringify(filters); // stable dep for effects

  const [events, setEvents] = useState<EventOut[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [facets, setFacets] = useState<EventFacets>({
    source_systems: [],
    event_types: [],
  });
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  // Live-tail handler reads the latest filters without re-subscribing.
  const filtersRef = useRef(filters);
  filtersRef.current = filters;

  const patchFilters = useCallback(
    (patch: Partial<EventQuery>) => {
      const next = new URLSearchParams(searchParams);
      for (const [k, v] of Object.entries(patch)) {
        if (v) next.set(k, String(v));
        else next.delete(k);
      }
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  // Facets once.
  useEffect(() => {
    api.getEventFacets().then(setFacets).catch(() => {});
  }, []);

  // (Re)load page 1 whenever the filters change.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .listEvents({ ...JSON.parse(filtersKey), limit: PAGE_SIZE })
      .then((page) => {
        if (cancelled) return;
        setEvents(page.events);
        setNextCursor(page.next_cursor);
      })
      .catch((e) => !cancelled && toast.error(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [filtersKey]);

  // Live tail: prepend matching INSERTs. The API remains the source of truth;
  // live rows show a fallback summary until a later fetch replaces them.
  useEffect(() => {
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;
    (async () => {
      try {
        const { token } = await api.getRealtimeToken();
        await supabase.realtime.setAuth(token);
      } catch {
        // Live updates are a convenience; the feed still loads without them.
      }
      if (cancelled) return;
      channel = supabase
        .channel("events-changes")
        .on(
          "postgres_changes",
          { event: "INSERT", schema: "public", table: "events" },
          (payload) => {
            const r = payload.new as Record<string, unknown>;
            const ev: EventOut = {
              id: String(r.id),
              created_at: String(r.created_at),
              source_system: String(r.source_system),
              event_type: String(r.event_type),
              entity_type: (r.entity_type as string | null) ?? null,
              entity_id: (r.entity_id as string | null) ?? null,
              payload: (r.payload as Record<string, unknown>) ?? {},
              summary: fallbackSummary({
                event_type: String(r.event_type),
                payload: (r.payload as Record<string, unknown>) ?? {},
              }),
            };
            if (!matchesFilters(ev, filtersRef.current)) return;
            setEvents((prev) =>
              prev.some((e) => e.id === ev.id) ? prev : [ev, ...prev],
            );
          },
        )
        .subscribe();
    })();
    return () => {
      cancelled = true;
      if (channel) supabase.removeChannel(channel);
    };
  }, []);

  const loadMore = async () => {
    if (!nextCursor) return;
    setLoadingMore(true);
    try {
      const page = await api.listEvents({
        ...filters,
        limit: PAGE_SIZE,
        cursor: nextCursor,
      });
      setEvents((prev) => {
        const seen = new Set(prev.map((e) => e.id));
        return [...prev, ...page.events.filter((e) => !seen.has(e.id))];
      });
      setNextCursor(page.next_cursor);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex h-14 shrink-0 items-center border-b px-6">
        <h1 className="text-lg font-semibold">Event Log</h1>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-4 p-6">
        <EventFilters facets={facets} filters={filters} onChange={patchFilters} />

        <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border">
          {loading ? (
            <div className="flex flex-col gap-3 p-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : events.length === 0 ? (
            <div className="p-10 text-center text-sm text-muted-foreground">
              No events match these filters.
            </div>
          ) : (
            <>
              {events.map((ev) => (
                <EventRow
                  key={ev.id}
                  event={ev}
                  onEntityClick={(entity_type, entity_id) =>
                    patchFilters({ entity_type, entity_id })
                  }
                />
              ))}
              {nextCursor && (
                <div className="flex justify-center p-4">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={loadMore}
                    disabled={loadingMore}
                  >
                    {loadingMore ? "Loading…" : "Load more"}
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
