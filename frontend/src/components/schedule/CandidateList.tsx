import { AlertTriangle } from "lucide-react";
import type { Candidate } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

// Ranked caregiver cards for an open shift: name, a score badge, plain-language
// reason chips, and any warnings in amber. Assign lives on each card. Purely
// presentational — the drawer fetches the ranking and owns the assign action, so
// the score is a small badge, never a table of numbers (the plain-copy rule).
export function CandidateList({
  candidates,
  loading,
  assigningId,
  onAssign,
}: {
  candidates: Candidate[];
  loading: boolean;
  assigningId: string | null;
  onAssign: (c: Candidate) => void;
}) {
  if (loading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-20 w-full" />
        ))}
      </div>
    );
  }
  if (candidates.length === 0) {
    return (
      <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
        No eligible caregivers for this shift — everyone is missing a required
        qualification or already booked over this window.
      </p>
    );
  }
  return (
    <div className="space-y-2">
      {candidates.map((c) => (
        <div key={c.resource_id} className="rounded-lg border bg-card p-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="truncate text-sm font-medium">{c.name}</span>
                <Badge variant="info" className="shrink-0 tabular-nums">
                  {c.score}
                </Badge>
              </div>
            </div>
            <Button
              size="sm"
              variant="outline"
              disabled={assigningId !== null}
              onClick={() => onAssign(c)}
            >
              {assigningId === c.resource_id ? "Assigning…" : "Assign"}
            </Button>
          </div>
          {c.reasons.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {c.reasons.map((r, i) => (
                <span
                  key={i}
                  className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground"
                >
                  {r}
                </span>
              ))}
            </div>
          )}
          {c.warnings.map((w, i) => (
            <div
              key={i}
              className="mt-1.5 flex items-center gap-1.5 text-[11px] text-warning"
            >
              <AlertTriangle className="h-3 w-3 shrink-0" />
              {w}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
