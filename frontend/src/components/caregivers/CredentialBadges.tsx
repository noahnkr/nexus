import type { Credential } from "@/lib/api";
import { cn } from "@/lib/utils";
import { credentialMeta, fmtDaysLeft, sortCredentials } from "@/lib/workforce";

// Compact per-credential chips for a roster row. Worst first (sortCredentials), so
// the one that needs attention is always the leftmost thing in the cell. Every
// tone and every date phrase comes from the server-derived `status` / `days_left`
// — no client-side expiry math (M18 rule).
const TONE: Record<string, string> = {
  destructive: "border-destructive/20 bg-destructive/10 text-destructive",
  warning: "border-warning/20 bg-warning/10 text-warning",
  success: "border-success/20 bg-success/10 text-success",
  secondary: "border-border bg-muted text-muted-foreground",
};

export function CredentialBadges({
  credentials,
  max,
  className,
}: {
  credentials: Credential[];
  max?: number; // cap the chips shown; the rest collapse into a "+N" chip
  className?: string;
}) {
  if (credentials.length === 0) {
    return <span className={cn("text-xs text-muted-foreground", className)}>None on file</span>;
  }

  const sorted = sortCredentials(credentials);
  const shown = max ? sorted.slice(0, max) : sorted;
  const hidden = sorted.length - shown.length;

  return (
    <div className={cn("flex flex-wrap items-center gap-1", className)}>
      {shown.map((c) => {
        const meta = credentialMeta(c.status);
        return (
          <span
            key={c.id}
            title={`${c.qualification_name} — ${meta.label}, ${fmtDaysLeft(c.days_left)}`}
            className={cn(
              "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
              TONE[meta.badge] ?? TONE.secondary,
            )}
          >
            <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", meta.dot)} />
            {c.qualification_name}
          </span>
        );
      })}
      {hidden > 0 && (
        <span className="text-[11px] text-muted-foreground">+{hidden}</span>
      )}
    </div>
  );
}
