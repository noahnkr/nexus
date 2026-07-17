import { Link } from "react-router-dom";
import { History } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ApprovalCard } from "./ApprovalCard";
import type { Task, TaskPriority, TaskStatus } from "@/lib/api";

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

const PRIORITY_VARIANT: Record<TaskPriority, "default" | "secondary" | "destructive" | "outline"> = {
  urgent: "destructive",
  high: "default",
  normal: "secondary",
  low: "outline",
};

const STATUS_VARIANT: Record<
  TaskStatus,
  "secondary" | "outline" | "success" | "warning" | "info"
> = {
  pending: "warning",
  in_progress: "info",
  done: "success",
  cancelled: "outline",
};

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: "Pending",
  in_progress: "In progress",
  done: "Done",
  cancelled: "Cancelled",
};

export function TaskCard({
  task,
  onTransition,
  onApprove,
  onReject,
}: {
  task: Task;
  onTransition: (id: string, status: TaskStatus) => void;
  onApprove: (id: string) => Promise<void>;
  onReject: (id: string, note?: string) => Promise<void>;
}) {
  const terminal = task.status === "done" || task.status === "cancelled";
  const hasPendingAction = task.pending_actions.some((a) => a.status === "pending");
  const closeHint = hasPendingAction
    ? "Resolve the approval below first"
    : undefined;

  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold">{task.title}</h3>
            {task.description && (
              <p className="mt-1 text-sm text-muted-foreground">{task.description}</p>
            )}
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
            <Badge variant={PRIORITY_VARIANT[task.priority]}>{task.priority}</Badge>
            <Badge variant={STATUS_VARIANT[task.status]}>
              {STATUS_LABEL[task.status]}
            </Badge>
          </div>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>Created {timeAgo(task.created_at)}</span>
          {task.due_at && <span>· Due {dueLabel(task.due_at)}</span>}
          <Link
            to={`/events?entity_type=task&entity_id=${task.id}`}
            className="inline-flex items-center gap-1 hover:text-foreground"
          >
            <History className="h-3 w-3" /> View history
          </Link>
          {task.originating_event_id && (
            <Link
              to={`/events?entity_type=task&entity_id=${task.id}`}
              className="hover:text-foreground"
              title="This task was created from an event"
            >
              · Originating event
            </Link>
          )}
        </div>

        {task.pending_actions.length > 0 && (
          <div className="mt-3 space-y-2">
            {task.pending_actions.map((a) => (
              <ApprovalCard
                key={a.id}
                action={a}
                description={task.description}
                onApprove={onApprove}
                onReject={onReject}
              />
            ))}
          </div>
        )}

        {!terminal && (
          <div className="mt-3 flex flex-wrap gap-2">
            {task.status === "pending" && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => onTransition(task.id, "in_progress")}
              >
                Start
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              disabled={hasPendingAction}
              title={closeHint}
              onClick={() => onTransition(task.id, "done")}
            >
              Done
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={hasPendingAction}
              title={closeHint}
              onClick={() => onTransition(task.id, "cancelled")}
            >
              Cancel
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
