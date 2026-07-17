import { useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Ban,
  Pause,
  X,
  AlertTriangle,
} from "lucide-react";
import type { ComponentType } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn, relativeTime } from "@/lib/utils";
import { isActiveRun, RUN_STATUS_META, type RunStatus } from "@/lib/recipe";
import type { Run, StepLogEntry } from "@/lib/api";

const STEP_STATUS: Record<
  StepLogEntry["status"],
  { icon: ComponentType<{ className?: string }>; tone: string }
> = {
  ok: { icon: Check, tone: "text-success" },
  queued: { icon: Pause, tone: "text-warning" },
  waiting: { icon: Clock, tone: "text-info" },
  stopped: { icon: Ban, tone: "text-muted-foreground" },
  failed: { icon: AlertTriangle, tone: "text-destructive" },
};

// A slide-over drawer showing one run's step-by-step timeline (from `step_log`),
// the accumulated `context` behind a technical expander, and a Cancel button while
// the run is still active. Read-only otherwise — cancellation routes through the
// M7 cancel endpoint via the page's handler.
export function RunTimeline({
  run,
  onClose,
  onCancel,
}: {
  run: Run;
  onClose: () => void;
  onCancel: (run: Run) => Promise<void>;
}) {
  const [showContext, setShowContext] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const meta = RUN_STATUS_META[run.status as RunStatus];
  const active = isActiveRun(run.status);

  const doCancel = async () => {
    setCancelling(true);
    try {
      await onCancel(run);
    } finally {
      setCancelling(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <aside
        className="flex h-full w-full max-w-md flex-col border-l bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b px-5 py-3">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold">Run detail</h2>
            <Badge variant={meta?.tone ?? "secondary"}>{meta?.label ?? run.status}</Badge>
          </div>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <p className="mb-4 text-[12px] text-muted-foreground">
            Started {relativeTime(run.created_at)}
            {run.finished_at && ` · finished ${relativeTime(run.finished_at)}`}
          </p>

          {run.error && (
            <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-[13px] text-destructive">
              {run.error}
            </div>
          )}

          {run.step_log.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">
              No steps have run yet.
            </p>
          ) : (
            <ol className="relative ml-3 space-y-4 border-l pl-5">
              {run.step_log.map((entry, i) => {
                const s = STEP_STATUS[entry.status] ?? STEP_STATUS.ok;
                const Icon = s.icon;
                return (
                  <li key={i} className="relative">
                    <span className="absolute -left-[27px] flex h-5 w-5 items-center justify-center rounded-full border bg-card">
                      <Icon className={cn("h-3 w-3", s.tone)} />
                    </span>
                    <p className="text-[13px] font-medium capitalize">{entry.type}</p>
                    <p className="text-[13px] text-muted-foreground">{entry.summary}</p>
                    <time className="text-[11px] text-muted-foreground/70">
                      {relativeTime(entry.at)}
                    </time>
                  </li>
                );
              })}
            </ol>
          )}

          <button
            onClick={() => setShowContext((v) => !v)}
            className="mt-5 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            {showContext ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            Technical detail (context)
          </button>
          {showContext && (
            <pre className="mt-1 overflow-x-auto rounded-md bg-muted p-2 text-xs text-muted-foreground">
              {JSON.stringify(run.context, null, 2)}
            </pre>
          )}
        </div>

        {active && (
          <footer className="border-t p-4">
            <Button
              variant="destructive"
              size="sm"
              onClick={doCancel}
              disabled={cancelling}
              className="w-full"
            >
              <Ban className="h-4 w-4" /> {cancelling ? "Cancelling…" : "Cancel run"}
            </Button>
          </footer>
        )}
      </aside>
    </div>
  );
}
