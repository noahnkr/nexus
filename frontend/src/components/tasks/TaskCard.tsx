import { Clock, History } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { taskKind } from "@/lib/tasks";
import type { Task } from "@/lib/api";
import { PRIORITY_VARIANT, STATUS_LABEL, STATUS_VARIANT } from "./taskMeta";

export { PRIORITY_DOT } from "./taskMeta";

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function dueLabel(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// The card is now a summary, not a workbench: type, title, status, and whether it
// needs approval. Reading the drafted message and resolving it happens in the
// drawer, which is why the raw-JSON expander and the inline approve buttons are
// gone from here.
export function TaskCard({ task, onOpen }: { task: Task; onOpen: (task: Task) => void }) {
  const kind = taskKind(task);
  const Icon = kind.icon;
  const awaiting = task.pending_actions.some((a) => a.status === "pending");

  return (
    <Card
      role="button"
      tabIndex={0}
      onClick={() => onOpen(task)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(task);
        }
      }}
      className="cursor-pointer transition-colors hover:border-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 gap-3">
            <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <h3 className="text-sm font-semibold">{task.title}</h3>
              {task.description && (
                <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                  {task.description}
                </p>
              )}
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
            <Badge variant={PRIORITY_VARIANT[task.priority]}>{task.priority}</Badge>
            <Badge variant={STATUS_VARIANT[task.status]}>{STATUS_LABEL[task.status]}</Badge>
          </div>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span className="rounded-full border px-2 py-0.5">{kind.label}</span>
          <span>Created {timeAgo(task.created_at)}</span>
          {task.due_at && (
            <>
              <span aria-hidden>·</span>
              <span>Due {dueLabel(task.due_at)}</span>
            </>
          )}
          {task.originating_event_id && (
            <>
              <span aria-hidden>·</span>
              <span className="inline-flex items-center gap-1" title="Created from an event">
                <History className="h-3 w-3" /> From an event
              </span>
            </>
          )}
        </div>

        {awaiting && (
          <div className="mt-3 inline-flex items-center gap-1.5 rounded-md border bg-muted/30 px-2.5 py-1.5 text-xs font-medium text-warning">
            <Clock className="h-3.5 w-3.5" />
            Awaiting your approval — open to review
          </div>
        )}
      </CardContent>
    </Card>
  );
}
