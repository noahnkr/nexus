import type { ComponentType } from "react";
import { AlertTriangle, Gauge, ShieldCheck, Users } from "lucide-react";
import type { RosterMetrics } from "@/lib/api";
import { cn } from "@/lib/utils";
import { fmtUtilization } from "@/lib/workforce";

// The Roster tab's headline numbers. Every value is server-computed
// (services/views/workforce.py) — this component only picks a tone and a label.
// Tiles mirror HiringMetrics so the two tabs read as one page.
type Tone = "default" | "info" | "success" | "warning" | "destructive";

const toneChip: Record<Tone, string> = {
  default: "bg-primary/10 text-primary",
  info: "bg-info/10 text-info",
  success: "bg-success/10 text-success",
  warning: "bg-warning/10 text-warning",
  destructive: "bg-destructive/10 text-destructive",
};

function Tile({
  label,
  value,
  sub,
  icon: Icon,
  tone = "default",
}: {
  label: string;
  value: string;
  sub?: string;
  icon: ComponentType<{ className?: string }>;
  tone?: Tone;
}) {
  return (
    <div className="flex flex-col justify-between gap-3 rounded-xl border bg-card p-4 shadow-sm">
      <span className={cn("flex h-8 w-8 items-center justify-center rounded-lg", toneChip[tone])}>
        <Icon className="h-4 w-4" />
      </span>
      <div>
        <div className="text-2xl font-semibold leading-none tracking-tight tabular-nums">
          {value}
        </div>
        <div className="mt-1 text-[12px] text-muted-foreground">{label}</div>
        {sub && <div className="mt-0.5 text-[11px] text-muted-foreground">{sub}</div>}
      </div>
    </div>
  );
}

export function ComplianceStrip({ metrics }: { metrics: RosterMetrics | null }) {
  const expiring = metrics?.expiring_count ?? 0;
  const expired = metrics?.expired_count ?? 0;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile
        label="Active caregivers"
        value={String(metrics?.active_count ?? 0)}
        sub={
          metrics && metrics.inactive_count > 0
            ? `${metrics.inactive_count} inactive`
            : undefined
        }
        icon={Users}
        tone="info"
      />
      <Tile
        label="Average utilization"
        value={fmtUtilization(metrics?.avg_utilization)}
        sub="of declared availability"
        icon={Gauge}
        tone="default"
      />
      {/* Tones go loud only when there is actually something to act on — a quiet
          compliance strip should look quiet. */}
      <Tile
        label="Expiring soon"
        value={String(expiring)}
        sub="within 60 days"
        icon={AlertTriangle}
        tone={expiring > 0 ? "warning" : "success"}
      />
      <Tile
        label="Expired"
        value={String(expired)}
        sub={expired > 0 ? "may block a shift" : "all current"}
        icon={ShieldCheck}
        tone={expired > 0 ? "destructive" : "success"}
      />
    </div>
  );
}
