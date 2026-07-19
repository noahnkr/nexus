import { barPct, fillMonths, monthLabel } from "@/lib/referrals";
import { cn } from "@/lib/utils";
import type { MonthCount } from "@/lib/api";

// Hand-rolled monthly bar row (no chart library — user decision). Used two ways:
//   * full — the page's overall lead trend + the drawer's per-partner trend, with
//     month labels under each bar.
//   * spark — the compact per-row sparkline in the partner table (no labels).
// Heights scale to the tallest bucket in the window; theme tokens keep it readable
// in both light and dark. Buckets are zero-filled so the window width is constant.
export function MonthlyTrendBars({
  monthly,
  months,
  variant = "full",
  className,
}: {
  monthly: MonthCount[];
  months?: string[]; // window to zero-fill against (defaults to the series' own months)
  variant?: "full" | "spark";
  className?: string;
}) {
  const window = months ?? monthly.map((b) => b.month);
  const buckets = fillMonths(monthly, window);
  const max = buckets.reduce((m, b) => Math.max(m, b.count), 0);
  const spark = variant === "spark";

  return (
    <div
      className={cn("flex items-end gap-1", spark ? "h-8" : "h-24", className)}
      aria-hidden={spark ? true : undefined}
    >
      {buckets.map((b) => (
        <div key={b.month} className="flex min-w-0 flex-1 flex-col items-center gap-1">
          <div
            className={cn(
              "flex w-full items-end justify-center rounded-sm bg-muted",
              spark ? "h-6" : "flex-1",
            )}
            title={`${monthLabel(b.month)}: ${b.count} lead${b.count === 1 ? "" : "s"}`}
          >
            <div
              className={cn(
                "w-full rounded-sm transition-[height]",
                b.count > 0 ? "bg-primary" : "bg-transparent",
              )}
              style={{ height: `${barPct(b.count, max)}%` }}
            />
          </div>
          {!spark && (
            <>
              <span className="text-[11px] tabular-nums font-medium text-foreground">
                {b.count}
              </span>
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                {monthLabel(b.month)}
              </span>
            </>
          )}
        </div>
      ))}
    </div>
  );
}
