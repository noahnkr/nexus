import { Card, CardContent } from "@/components/ui/card";
import { fmtHours } from "@/lib/clients";
import { cn } from "@/lib/utils";
import type { ClientHours } from "@/lib/api";

// This week's hours as three labeled bars scaled to the largest of the three, so
// the shortfall reads at a glance. The delivered bar turns warning-toned whenever
// it falls short of authorized — the leakage the census is built to surface. All
// figures come from the server's client_week_hours (no client-side math).
function Bar({
  label,
  hours,
  max,
  tone,
}: {
  label: string;
  hours: number;
  max: number;
  tone: "primary" | "info" | "success" | "warning";
}) {
  const pct = max > 0 ? Math.round((hours / max) * 100) : 0;
  const fill = {
    primary: "bg-primary",
    info: "bg-info",
    success: "bg-success",
    warning: "bg-warning",
  }[tone];
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums font-medium">{fmtHours(hours)}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div className={cn("h-full rounded-full transition-all", fill)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function HoursCard({ hours }: { hours: ClientHours }) {
  const max = Math.max(
    hours.authorized_hours,
    hours.scheduled_hours,
    hours.delivered_hours,
    0.1,
  );
  const short = hours.delivered_hours < hours.authorized_hours;

  return (
    <Card>
      <CardContent className="space-y-4 p-4">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            This week's hours
          </p>
          {hours.delivery_rate != null && (
            <span className="text-xs text-muted-foreground tabular-nums">
              {hours.delivery_rate}% delivered
            </span>
          )}
        </div>

        <div className="space-y-3">
          <Bar label="Authorized" hours={hours.authorized_hours} max={max} tone="primary" />
          <Bar label="Scheduled" hours={hours.scheduled_hours} max={max} tone="info" />
          <Bar
            label="Delivered"
            hours={hours.delivered_hours}
            max={max}
            tone={short ? "warning" : "success"}
          />
        </div>

        {hours.leakage_hours > 0 ? (
          <p className="text-sm text-warning">
            {fmtHours(hours.leakage_hours)} under authorized this week.
          </p>
        ) : hours.authorized_hours > 0 ? (
          <p className="text-sm text-success">Delivering all authorized hours this week.</p>
        ) : (
          <p className="text-sm text-muted-foreground">No authorized hours set yet.</p>
        )}
      </CardContent>
    </Card>
  );
}
