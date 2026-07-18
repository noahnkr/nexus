import { Link } from "react-router-dom";
import { Zap } from "lucide-react";
import { cn } from "@/lib/utils";
import type { StageTone } from "@/lib/pipeline";

// A generic, prop-driven funnel strip: ordered stage segments whose width reflects
// their share of the pipeline, a click that toggles the directory's stage filter,
// and an optional per-stage sequence chip. No vertical knowledge — Leads and (M10)
// Caregivers both render it from their own config.

export type SequenceState = "active" | "paused" | "none";

export interface FunnelSegment {
  key: string;
  label: string;
  tone: StageTone;
  count: number;
  // Present only for stages that can carry a sequence (config.sequenceStages).
  sequence?: { state: SequenceState; route: string };
}

const toneTint: Record<StageTone, string> = {
  default: "bg-primary/10 text-primary",
  info: "bg-info/10 text-info",
  success: "bg-success/10 text-success",
  secondary: "bg-muted text-muted-foreground",
};

function SequenceChip({
  state,
  route,
}: {
  state: SequenceState;
  route: string;
}) {
  const base =
    "mt-1.5 inline-flex w-full items-center justify-center gap-1 rounded-md border px-2 py-1 text-[11px] font-medium transition-colors";
  if (state === "active") {
    return (
      <Link to={route} className={cn(base, "border-primary/30 bg-primary/10 text-primary hover:bg-primary/15")}>
        <Zap className="h-3 w-3" /> Sequence
      </Link>
    );
  }
  if (state === "paused") {
    return (
      <Link to={route} className={cn(base, "border-input bg-muted/60 text-muted-foreground hover:bg-muted")}>
        <Zap className="h-3 w-3" /> Paused
      </Link>
    );
  }
  return (
    <Link
      to={route}
      className={cn(base, "border-dashed border-input text-muted-foreground hover:border-primary/40 hover:text-foreground")}
    >
      ＋ Sequence
    </Link>
  );
}

export function FunnelStrip({
  segments,
  active,
  onSelect,
}: {
  segments: FunnelSegment[];
  active: string; // active status filter key ("" = all)
  onSelect: (key: string) => void; // toggles the filter
}) {
  const total = segments.reduce((sum, s) => sum + s.count, 0) || 1;

  return (
    <div className="flex flex-wrap items-stretch gap-2 sm:flex-nowrap">
      {segments.map((seg) => {
        const pct = Math.round((100 * seg.count) / total);
        const isActive = seg.key === active;
        // Weight width by share, floored so an empty stage still reads.
        const grow = Math.max(seg.count, total * 0.08);
        return (
          <div
            key={seg.key}
            className="flex min-w-[9rem] flex-col sm:min-w-0"
            style={{ flexGrow: grow, flexBasis: 0 }}
          >
            <button
              onClick={() => onSelect(isActive ? "" : seg.key)}
              className={cn(
                "flex w-full flex-col items-start gap-1 rounded-lg border p-3 text-left transition-all",
                isActive
                  ? "border-primary ring-2 ring-primary/30"
                  : "border-border hover:border-primary/40",
              )}
            >
              <span
                className={cn(
                  "rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
                  toneTint[seg.tone],
                )}
              >
                {seg.label}
              </span>
              <span className="text-2xl font-semibold leading-none tabular-nums">
                {seg.count}
              </span>
              <span className="text-[11px] text-muted-foreground">{pct}% of pipeline</span>
            </button>
            {seg.sequence && (
              <SequenceChip state={seg.sequence.state} route={seg.sequence.route} />
            )}
          </div>
        );
      })}
    </div>
  );
}
