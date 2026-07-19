import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Check, History, Pencil, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { displayFields, fieldLabel, taskKind, toolLabel } from "@/lib/tasks";
import type { PendingAction, Task, TaskStatus } from "@/lib/api";
import { STATUS_LABEL, STATUS_VARIANT, PRIORITY_VARIANT } from "./taskMeta";

// Right-side sheet for one task, following the VisitDrawer pattern (M12b). This is
// where an approval is actually read and resolved: the queued call is rendered as
// labeled fields (To / Message / Subject), the fields the tool allows are editable
// in place, and the raw payload survives only in the collapsed expander at the
// bottom. Cards never show JSON — that was the whole complaint.
export function TaskDrawer({
  task,
  onClose,
  onTransition,
  onApprove,
  onReject,
}: {
  task: Task;
  onClose: () => void;
  onTransition: (id: string, status: TaskStatus) => Promise<void> | void;
  onApprove: (id: string, edits?: Record<string, string>) => Promise<void>;
  onReject: (id: string, note?: string) => Promise<void>;
}) {
  const kind = taskKind(task);
  const Icon = kind.icon;
  const terminal = task.status === "done" || task.status === "cancelled";
  const hasPendingAction = task.pending_actions.some((a) => a.status === "pending");
  const closeHint = hasPendingAction ? "Resolve the approval first" : undefined;

  return (
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="absolute right-0 top-0 flex h-full w-full max-w-md flex-col border-l bg-card shadow-xl">
        <div className="flex items-start justify-between gap-3 border-b p-4">
          <div className="flex min-w-0 gap-3">
            <Icon className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <h2 className="text-base font-semibold">{task.title}</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">{kind.label}</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Badge variant={STATUS_VARIANT[task.status]}>
              {STATUS_LABEL[task.status]}
            </Badge>
            <button
              onClick={onClose}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
          {task.description && (
            <p className="text-sm text-muted-foreground">{task.description}</p>
          )}

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge variant={PRIORITY_VARIANT[task.priority]}>{task.priority}</Badge>
            {task.due_at && (
              <span className="text-muted-foreground">
                Due {new Date(task.due_at).toLocaleString(undefined, {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            )}
          </div>

          {task.pending_actions.map((action) => (
            <ActionPanel
              key={action.id}
              action={action}
              onApprove={onApprove}
              onReject={onReject}
            />
          ))}

          {!terminal && (
            <div className="flex flex-wrap gap-2 border-t pt-4">
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

          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t pt-4 text-xs text-muted-foreground">
            <Link
              to={`/events?entity_type=task&entity_id=${task.id}`}
              className="inline-flex items-center gap-1 hover:text-foreground"
            >
              <History className="h-3 w-3" /> View history
            </Link>
            {task.originating_event_id && (
              <>
                <span aria-hidden>·</span>
                <Link
                  to={`/events?entity_type=task&entity_id=${task.id}`}
                  className="hover:text-foreground"
                  title="This task was created from an event"
                >
                  Originating event
                </Link>
              </>
            )}
          </div>

          {task.pending_actions.length > 0 && (
            <TechnicalDetail actions={task.pending_actions} />
          )}
        </div>
      </div>
    </div>
  );
}

// One queued (or resolved) action: plain-language fields, in-place editing of the
// fields the tool allows, then approve/reject.
function ActionPanel({
  action,
  onApprove,
  onReject,
}: {
  action: PendingAction;
  onApprove: (id: string, edits?: Record<string, string>) => Promise<void>;
  onReject: (id: string, note?: string) => Promise<void>;
}) {
  const pending = action.status === "pending";
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");

  // Seed the editable fields from the draft the agent wrote. Keyed on id/status
  // ONLY: the Tasks page refetches on every Realtime signal and hands back a new
  // `action` object each time, so depending on tool_input/editable_fields here
  // would wipe whatever the user is in the middle of typing.
  useEffect(() => {
    const seed: Record<string, string> = {};
    for (const key of action.editable_fields) {
      const value = action.tool_input[key];
      if (typeof value === "string") seed[key] = value;
    }
    setDraft(seed);
    setRejecting(false);
    setNote("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action.id, action.status]);

  const editable = new Set(pending ? action.editable_fields : []);
  const dirty = Object.entries(draft).filter(
    ([k, v]) => v !== action.tool_input[k],
  );
  const blank = Object.values(draft).some((v) => !v.trim());

  const doApprove = async () => {
    setBusy(true);
    try {
      await onApprove(
        action.id,
        dirty.length ? Object.fromEntries(dirty) : undefined,
      );
    } finally {
      setBusy(false);
    }
  };

  const doReject = async () => {
    setBusy(true);
    try {
      await onReject(action.id, note.trim() || undefined);
    } finally {
      setBusy(false);
      setRejecting(false);
    }
  };

  return (
    <div className="rounded-lg border bg-muted/30 p-3">
      <div className="mb-3 flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{toolLabel(action.tool_name)}</span>
        {pending && <Badge variant="warning">Awaiting approval</Badge>}
      </div>

      <div className="space-y-3">
        {displayFields(action).map(([key, value]) =>
          editable.has(key) ? (
            <div key={key}>
              <div className="mb-1 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {fieldLabel(key)}
                <Pencil className="h-3 w-3" aria-label="editable" />
              </div>
              <Textarea
                value={draft[key] ?? ""}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                rows={key === "body" ? 4 : 1}
                className="min-h-0 resize-none text-sm"
                disabled={busy}
              />
            </div>
          ) : (
            <div key={key}>
              <div className="mb-0.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {fieldLabel(key)}
              </div>
              <div className="whitespace-pre-wrap break-words text-sm">{value}</div>
            </div>
          ),
        )}
      </div>

      {pending ? (
        <div className="mt-3 space-y-2">
          {rejecting && (
            <input
              autoFocus
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Optional note (why is this rejected?)"
              className="flex h-8 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              onClick={doApprove}
              disabled={busy || blank}
              title={blank ? "Fill in every field before approving" : undefined}
            >
              <Check className="h-3.5 w-3.5" />
              {dirty.length ? "Approve with edits" : "Approve"}
            </Button>
            {rejecting ? (
              <>
                <Button size="sm" variant="destructive" onClick={doReject} disabled={busy}>
                  Confirm reject
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setRejecting(false)}
                  disabled={busy}
                >
                  Cancel
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                variant="outline"
                onClick={() => setRejecting(true)}
                disabled={busy}
              >
                <X className="h-3.5 w-3.5" /> Reject
              </Button>
            )}
          </div>
        </div>
      ) : (
        <ResolvedLine action={action} />
      )}
    </div>
  );
}

export function ResolvedLine({ action }: { action: PendingAction }) {
  const { status, result } = action;
  const failed = status === "failed";
  const rejected = status === "rejected";
  const text = (failed ? result?.error : result?.summary) ?? (rejected ? "Rejected" : status);

  return (
    <div className="mt-3 space-y-1">
      <p className={failed || rejected ? "text-sm text-destructive" : "text-sm text-muted-foreground"}>
        {status === "executed" && "✓ Approved — "}
        {failed && "Approved, but failed — "}
        {rejected && "✕ Rejected — "}
        {text}
      </p>
      {result?.edited && (
        <p className="text-xs text-muted-foreground">
          Edited before approval: {(result.edited_fields ?? []).map(fieldLabel).join(", ")}
        </p>
      )}
      {action.resolved_by && (
        <p className="text-xs text-muted-foreground">by {action.resolved_by}</p>
      )}
    </div>
  );
}

// The one raw-JSON surface left in the task UI.
function TechnicalDetail({ actions }: { actions: PendingAction[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-t pt-3">
      <button
        onClick={() => setOpen((v) => !v)}
        className="text-xs text-muted-foreground hover:text-foreground"
      >
        {open ? "Hide" : "Show"} technical detail
      </button>
      {open && (
        <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-2 text-[11px] leading-relaxed">
          {JSON.stringify(
            actions.map((a) => ({ tool_name: a.tool_name, tool_input: a.tool_input })),
            null,
            2,
          )}
        </pre>
      )}
    </div>
  );
}
