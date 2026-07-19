import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import { api, type ScheduleVisit } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDayTime, formatRange, statusMeta, weekStartOf } from "@/lib/schedule";
import { fmtDuration } from "@/lib/clients";
import { EvvBadge } from "@/components/schedule/EvvBadge";

// The next few upcoming visits and the last few past ones. Each row shows the
// status pill and — for a scheduled visit past its grace window — the amber EVV
// badge; once clocked, the actual duration. "Open in schedule" jumps to the board
// on that visit's week. Data from GET /api/clients/{id}/visits (board shape, so
// the EVV flag matches the board exactly).
function VisitRow({ visit }: { visit: ScheduleVisit }) {
  const meta = statusMeta(visit.status);
  const week = weekStartOf(new Date(visit.start_time));
  const duration = fmtDuration(visit.check_in_at, visit.check_out_at);
  return (
    <li className="flex items-start justify-between gap-3 py-2.5 first:pt-0">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-sm font-medium">{formatDayTime(visit.start_time)}</span>
          <span className="text-xs text-muted-foreground tabular-nums">{formatRange(visit)}</span>
          <Badge variant={meta.badge}>{meta.label}</Badge>
          <EvvBadge evv={visit.evv} />
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {visit.resource_name ?? "Unassigned"}
          {duration && <span> · clocked {duration}</span>}
        </p>
      </div>
      <Link
        to={`/schedule?week=${week}`}
        className="inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground hover:text-primary"
        aria-label="Open in schedule"
      >
        <ArrowUpRight className="h-3.5 w-3.5" />
      </Link>
    </li>
  );
}

export function ClientVisitsCard({
  clientId,
  refreshKey = 0,
}: {
  clientId: string;
  refreshKey?: number;
}) {
  const [upcoming, setUpcoming] = useState<ScheduleVisit[]>([]);
  const [past, setPast] = useState<ScheduleVisit[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const res = await api.getClientVisits(clientId, { upcoming: 5, past: 5 });
    setUpcoming(res.upcoming);
    setPast(res.past);
  }, [clientId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    load()
      .catch(() => {})
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [load, refreshKey]);

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Visits
        </p>

        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : upcoming.length === 0 && past.length === 0 ? (
          <p className="text-sm text-muted-foreground">No visits scheduled for this client yet.</p>
        ) : (
          <div className="space-y-4">
            {upcoming.length > 0 && (
              <div>
                <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Upcoming
                </p>
                <ul className="divide-y">
                  {upcoming.map((v) => (
                    <VisitRow key={v.id} visit={v} />
                  ))}
                </ul>
              </div>
            )}
            {past.length > 0 && (
              <div>
                <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Recent
                </p>
                <ul className="divide-y">
                  {past.map((v) => (
                    <VisitRow key={v.id} visit={v} />
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
