import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { formatDayTime, weekStartOf } from "@/lib/schedule";
import type { ClientCaregiverRef } from "@/lib/api";

// The caregivers currently serving this client (distinct, from their live visits in
// a ±30-day window), each with their next upcoming visit. Each row links to the
// Schedule board on the week of that next visit, so a coordinator can jump straight
// to it. Data comes from the client detail — no extra fetch.
export function CaregiversCard({ caregivers }: { caregivers: ClientCaregiverRef[] }) {
  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Caregivers
        </p>
        {caregivers.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No caregivers assigned in the last month.
          </p>
        ) : (
          <ul className="divide-y">
            {caregivers.map((c) => {
              const week = c.next_visit ? weekStartOf(new Date(c.next_visit)) : undefined;
              return (
                <li key={c.resource_id} className="flex items-center justify-between gap-3 py-2.5 first:pt-0">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{c.name}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {c.next_visit
                        ? `Next visit ${formatDayTime(c.next_visit)}`
                        : "No upcoming visit"}
                    </p>
                  </div>
                  <Link
                    to={week ? `/schedule?week=${week}` : "/schedule"}
                    className="inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground hover:text-primary"
                  >
                    Schedule <ArrowUpRight className="h-3.5 w-3.5" />
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
