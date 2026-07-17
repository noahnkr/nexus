import { Link } from "react-router-dom";
import { ArrowRight, Activity } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import { relativeTime } from "@/lib/utils";
import type { EventOut } from "@/lib/api";

// The last handful of events as plain-language summaries — no payloads or drill-in
// here (that's the Event Log's job). A read-only glance at what the system has been
// doing, with a footer link into the full log.
export function RecentActivity({
  events,
  loading,
}: {
  events: EventOut[];
  loading: boolean;
}) {
  return (
    <section className="flex flex-col overflow-hidden rounded-xl border bg-card shadow-sm">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <h2 className="text-[13px] font-semibold">Recent activity</h2>
        <Link
          to="/events"
          className="inline-flex items-center gap-1 text-[12px] font-medium text-muted-foreground transition-colors hover:text-primary"
        >
          Open Event Log
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>

      {loading ? (
        <div className="flex flex-col gap-3 p-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3">
              <Skeleton className="h-4 w-16" />
              <Skeleton className="h-4 flex-1" />
            </div>
          ))}
        </div>
      ) : events.length === 0 ? (
        <div className="p-4">
          <EmptyState
            icon={Activity}
            title="Nothing yet"
            description="Tool calls, uploads, and approvals will show up here as they happen."
          />
        </div>
      ) : (
        <ul className="divide-y">
          {events.map((ev) => (
            <li
              key={ev.id}
              className="flex items-center gap-3 px-4 py-2.5 text-[13px]"
            >
              <Badge
                variant="outline"
                className="shrink-0 font-normal text-muted-foreground"
              >
                {ev.source_system}
              </Badge>
              <span className="min-w-0 flex-1 truncate">{ev.summary}</span>
              <time className="shrink-0 text-[12px] tabular-nums text-muted-foreground">
                {relativeTime(ev.created_at)}
              </time>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
