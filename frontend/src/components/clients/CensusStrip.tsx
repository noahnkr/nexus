import type { ComponentType } from "react";
import { CalendarClock, HeartPulse, Timer, Users } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtHours, payerLabel } from "@/lib/clients";
import type { CensusMetrics } from "@/lib/api";

// The census strip: four stat tiles + by-payer / by-region chip rows. The number
// that matters is LEAKAGE — authorized (paid-for) hours minus delivered — so the
// delivered tile turns warning-toned whenever any leakage exists. Every figure
// comes straight from GET /api/clients/metrics; no client-side math beyond
// formatting (CLAUDE.md: no LLM/derived numbers near the census).
//
// These are non-navigational figures, so — like HiringMetrics — this uses local
// stat tiles rather than the home StatCard (which is a Link into a view).
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
  sublabel,
  icon: Icon,
  tone = "default",
}: {
  label: string;
  value: string;
  sublabel?: string;
  icon: ComponentType<{ className?: string }>;
  tone?: Tone;
}) {
  return (
    <div className="flex flex-col justify-between gap-3 rounded-xl border bg-card p-4 shadow-sm">
      <span
        className={cn("flex h-8 w-8 items-center justify-center rounded-lg", toneChip[tone])}
      >
        <Icon className="h-4 w-4" />
      </span>
      <div>
        <div className="text-2xl font-semibold leading-none tracking-tight tabular-nums">
          {value}
        </div>
        <div className="mt-1 text-[12px] font-medium text-foreground">{label}</div>
        {sublabel && (
          <div className="mt-0.5 text-[12px] text-muted-foreground">{sublabel}</div>
        )}
      </div>
    </div>
  );
}

function ChipRow({
  title,
  chips,
}: {
  title: string;
  chips: { key: string; label: string; count: number; active: boolean; onClick: () => void }[];
}) {
  if (chips.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="mr-0.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </span>
      {chips.map((c) => (
        <button
          key={c.key}
          type="button"
          onClick={c.onClick}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors",
            c.active
              ? "border-primary bg-primary/10 text-primary"
              : "border-input text-muted-foreground hover:border-primary/40 hover:text-foreground",
          )}
        >
          <span>{c.label}</span>
          <span className="tabular-nums font-medium">{c.count}</span>
        </button>
      ))}
    </div>
  );
}

export function CensusStrip({
  metrics,
  activePayer,
  activeRegion,
  onFilterPayer,
  onFilterRegion,
}: {
  metrics: CensusMetrics | null;
  activePayer: string;
  activeRegion: string;
  onFilterPayer: (payer: string | undefined) => void;
  onFilterRegion: (regionId: string | undefined) => void;
}) {
  const leakage = metrics?.leakage_hours ?? 0;
  const rate = metrics?.delivery_rate;

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile
          label="Active clients"
          value={String(metrics?.active_clients ?? 0)}
          icon={Users}
          tone="info"
        />
        <Tile
          label="Authorized / week"
          value={fmtHours(metrics?.authorized_hours)}
          icon={HeartPulse}
          tone="default"
        />
        <Tile
          label="Scheduled this week"
          value={fmtHours(metrics?.scheduled_hours)}
          sublabel={
            metrics && metrics.open_hours > 0
              ? `${fmtHours(metrics.open_hours)} unfilled`
              : "all shifts filled"
          }
          icon={CalendarClock}
          tone="info"
        />
        <Tile
          label="Delivered this week"
          value={fmtHours(metrics?.delivered_hours)}
          sublabel={
            leakage > 0
              ? `${fmtHours(leakage)} under authorized${rate != null ? ` · ${rate}% delivered` : ""}`
              : rate != null
                ? `${rate}% of authorized delivered`
                : "no authorized hours yet"
          }
          icon={Timer}
          tone={leakage > 0 ? "warning" : "success"}
        />
      </div>

      <ChipRow
        title="By payer"
        chips={(metrics?.by_payer ?? []).map((p) => ({
          key: p.payer,
          label: payerLabel(p.payer === "unknown" ? null : p.payer),
          count: p.count,
          // 'unknown' has no filterable payer value — clicking clears the filter.
          active: p.payer !== "unknown" && activePayer === p.payer,
          onClick: () =>
            p.payer === "unknown"
              ? onFilterPayer(undefined)
              : onFilterPayer(activePayer === p.payer ? undefined : p.payer),
        }))}
      />
      <ChipRow
        title="By region"
        chips={(metrics?.by_region ?? []).map((r) => ({
          key: r.region_id ?? "unassigned",
          label: r.region,
          count: r.count,
          active: r.region_id != null && activeRegion === r.region_id,
          onClick: () =>
            r.region_id == null
              ? onFilterRegion(undefined)
              : onFilterRegion(activeRegion === r.region_id ? undefined : r.region_id),
        }))}
      />
    </div>
  );
}
