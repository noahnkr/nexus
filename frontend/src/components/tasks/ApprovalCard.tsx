import { useState } from "react";
import { Check, ChevronDown, ChevronRight, Clock, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { PendingAction } from "@/lib/api";

// Plain-language names for the gated tools — never show raw tool names to staff.
const TOOL_LABELS: Record<string, string> = {
  update_lead_status: "Update lead status",
  update_client_status: "Update client status",
  create_schedule: "Schedule a visit",
  cancel_schedule: "Cancel a visit",
  send_sms: "Send a text message",
  send_email: "Send an email",
};

function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name.replace(/_/g, " ");
}

export function ApprovalCard({
  action,
  description,
  onApprove,
  onReject,
}: {
  action: PendingAction;
  description: string | null;
  onApprove: (id: string) => Promise<void>;
  onReject: (id: string, note?: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [showDetail, setShowDetail] = useState(false);

  const pending = action.status === "pending";

  const doApprove = async () => {
    setBusy(true);
    try {
      await onApprove(action.id);
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
    <div className="rounded-md border bg-muted/30 p-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Clock className="h-4 w-4 text-warning" />
        {toolLabel(action.tool_name)}
      </div>

      {description && (
        <p className="mt-1 text-sm text-muted-foreground">{description}</p>
      )}

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
          <div className="flex gap-2">
            <Button size="sm" onClick={doApprove} disabled={busy}>
              <Check className="h-3.5 w-3.5" /> Approve
            </Button>
            {rejecting ? (
              <>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={doReject}
                  disabled={busy}
                >
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

      <button
        onClick={() => setShowDetail((v) => !v)}
        className="mt-2 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        {showDetail ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        Technical detail
      </button>
      {showDetail && (
        <pre className="mt-1 overflow-x-auto rounded-md bg-muted p-2 text-xs text-muted-foreground">
          {JSON.stringify(action.tool_input, null, 2)}
        </pre>
      )}
    </div>
  );
}

function ResolvedLine({ action }: { action: PendingAction }) {
  const { status, result } = action;
  const failed = status === "failed";
  const rejected = status === "rejected";
  const text =
    (failed ? result?.error : result?.summary) ??
    (rejected ? "Rejected" : status);

  return (
    <p
      className={cn(
        "mt-2 text-sm",
        failed || rejected ? "text-destructive" : "text-muted-foreground",
      )}
    >
      {status === "executed" && "✓ Approved — "}
      {failed && "Approved, but failed — "}
      {rejected && "✕ Rejected — "}
      {text}
    </p>
  );
}
