import type { ComponentType } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/utils";

// One at-a-glance count on Home, linking into its full view. `tone` tints the icon
// chip with a semantic status token so a non-zero failed/pending count reads at a
// glance without shouting when everything is calm.
type Tone = "default" | "warning" | "success" | "info";

const toneChip: Record<Tone, string> = {
  default: "bg-primary/10 text-primary",
  warning: "bg-warning/10 text-warning",
  success: "bg-success/10 text-success",
  info: "bg-info/10 text-info",
};

export function StatCard({
  to,
  label,
  count,
  icon: Icon,
  sublabel,
  tone = "default",
  loading,
}: {
  to: string;
  label: string;
  count: number;
  icon: ComponentType<{ className?: string }>;
  sublabel?: string;
  tone?: Tone;
  loading?: boolean;
}) {
  return (
    <Link
      to={to}
      className="group relative flex flex-col justify-between gap-6 overflow-hidden rounded-xl border bg-card p-4 shadow-sm transition-all hover:border-primary/40 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex items-start justify-between">
        <span
          className={cn(
            "flex h-9 w-9 items-center justify-center rounded-lg",
            toneChip[tone],
          )}
        >
          <Icon className="h-[18px] w-[18px]" />
        </span>
        <ArrowUpRight className="h-4 w-4 text-muted-foreground/50 transition-colors group-hover:text-primary" />
      </div>
      <div>
        <div className="text-[28px] font-semibold leading-none tracking-tight tabular-nums">
          {loading ? (
            <span className="inline-block h-7 w-10 animate-pulse rounded bg-muted align-middle" />
          ) : (
            count
          )}
        </div>
        <div className="mt-1.5 text-[13px] font-medium text-foreground">
          {label}
        </div>
        {sublabel && (
          <div className="mt-0.5 text-[12px] text-muted-foreground">
            {sublabel}
          </div>
        )}
      </div>
    </Link>
  );
}
