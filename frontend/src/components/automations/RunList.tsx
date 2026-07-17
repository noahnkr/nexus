import { AlertCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { relativeTime } from "@/lib/utils";
import { RUN_STATUS_META } from "@/lib/recipe";
import type { Run } from "@/lib/api";

// Newest-first run history for an automation. A row shows the run's status, when it
// started/finished, its trigger source, and an error line when failed. Clicking a
// row opens the timeline drawer (RunTimeline).
export function RunList({
  runs,
  onSelect,
}: {
  runs: Run[];
  onSelect: (run: Run) => void;
}) {
  return (
    <ul className="divide-y rounded-lg border bg-card">
      {runs.map((run) => {
        const meta = RUN_STATUS_META[run.status];
        return (
          <li key={run.id}>
            <button
              onClick={() => onSelect(run)}
              className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50"
            >
              <Badge variant={meta?.tone ?? "secondary"} className="shrink-0">
                {meta?.label ?? run.status}
              </Badge>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px]">
                  {run.entity_type
                    ? `${run.entity_type} run`
                    : run.trigger_event_id
                      ? "Event-triggered run"
                      : "Manual run"}
                  {run.error && (
                    <span className="ml-2 inline-flex items-center gap-1 text-destructive">
                      <AlertCircle className="h-3.5 w-3.5" /> {run.error}
                    </span>
                  )}
                </p>
                <p className="text-[12px] text-muted-foreground">
                  Started {relativeTime(run.created_at)}
                  {run.finished_at && ` · finished ${relativeTime(run.finished_at)}`}
                </p>
              </div>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
