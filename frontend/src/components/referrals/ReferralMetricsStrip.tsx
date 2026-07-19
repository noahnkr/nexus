import type { ComponentType } from "react";
import { Award, Handshake, Timer, TrendingUp } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtHoursWon, fmtRate } from "@/lib/referrals";
import type { ReferralTotals } from "@/lib/api";

// The referrals strip: four stat tiles summarising the referral book. Every figure
// comes straight from GET /api/referrals/metrics — no client-side math beyond
// formatting (CLAUDE.md: no LLM/derived numbers near the metrics). Non-navigational
// tiles, mirroring CensusStrip / HiringMetrics.
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

export function ReferralMetricsStrip({ totals }: { totals: ReferralTotals | null }) {
  const best = totals?.best_converter ?? null;
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile
        label="Tracked partners"
        value={String(totals?.tracked_partners ?? 0)}
        icon={Handshake}
        tone="default"
      />
      <Tile
        label="Leads (last 30 days)"
        value={String(totals?.leads_last_30_days ?? 0)}
        sublabel="across all sources"
        icon={TrendingUp}
        tone="info"
      />
      <Tile
        label="Best converter"
        value={best ? fmtRate(best.conversion_rate) : "—"}
        sublabel={best ? best.source : "not enough data yet"}
        icon={Award}
        tone="success"
      />
      <Tile
        label="Hours/wk won"
        value={fmtHoursWon(totals?.total_hours_won)}
        sublabel="authorized hours from referrals"
        icon={Timer}
        tone="info"
      />
    </div>
  );
}
