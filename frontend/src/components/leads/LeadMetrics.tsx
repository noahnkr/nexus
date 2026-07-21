import { Users, TrendingUp, Sparkle, Timer } from "lucide-react";
import type { ComponentType } from "react";
import { cn } from "@/lib/utils";
import type { LeadMetrics as LeadMetricsData } from "@/lib/api";
import { IN_PIPELINE_STAGES } from "@/lib/leads";

// Conversion widgets beside the funnel: four semantic stat tiles + a top-sources
// card. No charts this phase (locked) — tones carry the meaning. Tiles are
// non-navigational (unlike Home's StatCard) and render percentages / floats, so
// they use a small local tile rather than the integer-count StatCard.
type Tone = "default" | "info" | "success" | "warning";

const toneChip: Record<Tone, string> = {
  default: "bg-primary/10 text-primary",
  info: "bg-info/10 text-info",
  success: "bg-success/10 text-success",
  warning: "bg-warning/10 text-warning",
};

function Tile({
  label,
  value,
  icon: Icon,
  tone = "default",
}: {
  label: string;
  value: string;
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
      </div>
    </div>
  );
}

export function LeadMetrics({ metrics }: { metrics: LeadMetricsData | null }) {
  // Non-terminal stages only, taken from the seam config rather than named here.
  const inPipeline =
    metrics?.stages
      .filter((s) => (IN_PIPELINE_STAGES as string[]).includes(s.stage))
      .reduce((sum, s) => sum + s.count, 0) ?? 0;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
      <Tile label="In pipeline" value={String(inPipeline)} icon={Users} tone="info" />
      <Tile
        label="Conversion rate"
        value={`${metrics?.conversion_rate ?? 0}%`}
        icon={TrendingUp}
        tone="success"
      />
      <Tile
        label="New this week"
        value={String(metrics?.new_last_7_days ?? 0)}
        icon={Sparkle}
        tone="default"
      />
      <Tile
        label="Avg days to convert"
        value={metrics?.avg_days_to_convert != null ? String(metrics.avg_days_to_convert) : "—"}
        icon={Timer}
        tone="warning"
      />
      <div className="col-span-2 flex flex-col gap-1.5 rounded-xl border bg-card p-4 shadow-sm lg:col-span-1">
        <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Top sources
        </div>
        {metrics && metrics.top_sources.length > 0 ? (
          <ul className="space-y-1">
            {metrics.top_sources.map((s) => (
              <li key={s.source} className="flex items-center justify-between text-[13px]">
                <span className="truncate text-foreground">{s.source}</span>
                <span className="tabular-nums text-muted-foreground">{s.count}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[13px] text-muted-foreground">No sources yet.</p>
        )}
      </div>
    </div>
  );
}
