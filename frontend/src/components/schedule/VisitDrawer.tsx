import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { ArrowRight, Check, MessageSquarePlus, X } from "lucide-react";
import { api, type Candidate, type ScheduleBoard, type ScheduleVisit } from "@/lib/api";
import { formatDayTime, formatRange, statusMeta } from "@/lib/schedule";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ConfirmDialog } from "@/components/automations/ConfirmDialog";
import { CandidateList } from "./CandidateList";

// Right-side sheet for one visit. Read-only header/body + state-driven actions:
// open shifts rank candidates → assign → an optional gated-SMS notify prompt;
// scheduled visits call out (drawer follows to the replacement), reassign, cancel,
// or record an outcome. Every action delegates to the API (source_system='user');
// notify is the one gated path (send_sms through the approval queue).
function firstName(name: string): string {
  return name.split(" ")[0] || name;
}

function defaultMessage(visit: ScheduleVisit, name: string): string {
  return (
    `Hi ${firstName(name)}, can you cover ${visit.client_name}'s visit on ` +
    `${formatDayTime(visit.start_time)}? Let me know — thanks!`
  );
}

export function VisitDrawer({
  board,
  visitId,
  onClose,
  onRefresh,
  onSelectVisit,
}: {
  board: ScheduleBoard;
  visitId: string;
  onClose: () => void;
  onRefresh: () => Promise<void>;
  onSelectVisit: (id: string) => void;
}) {
  const visit = board.visits.find((v) => v.id === visitId) ?? null;

  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [candLoading, setCandLoading] = useState(false);
  const [reassigning, setReassigning] = useState(false);
  const [assigningId, setAssigningId] = useState<string | null>(null);
  const [notify, setNotify] = useState<{ resourceId: string; name: string; message: string } | null>(null);
  const [queuedTaskId, setQueuedTaskId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<"callout" | "cancel" | null>(null);
  const [showTech, setShowTech] = useState(false);

  // Reset per-visit transient state whenever the drawer points at a new visit.
  useEffect(() => {
    setReassigning(false);
    setNotify(null);
    setQueuedTaskId(null);
    setConfirm(null);
    setShowTech(false);
  }, [visitId]);

  // Fetch ranked candidates for an open shift, or when reassigning a scheduled one.
  const status = visit?.status;
  const currentResource = visit?.resource_id ?? null;
  useEffect(() => {
    if (!visit) return;
    const want = status === "open" || reassigning;
    if (!want) {
      setCandidates([]);
      return;
    }
    let cancelled = false;
    setCandLoading(true);
    api
      .getCandidates(visit.id)
      .then((res) => {
        if (cancelled) return;
        // On reassign, drop the current holder from the list.
        setCandidates(res.candidates.filter((c) => c.resource_id !== currentResource));
      })
      .catch(() => !cancelled && setCandidates([]))
      .finally(() => !cancelled && setCandLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visit?.id, status, reassigning, currentResource]);

  if (!visit) return null;
  const meta = statusMeta(visit.status);
  const isPast = new Date(visit.end_time).getTime() < Date.now();
  const replacement = board.visits.find((v) => v.replaces_schedule_id === visit.id) ?? null;
  const original = visit.replaces_schedule_id
    ? board.visits.find((v) => v.id === visit.replaces_schedule_id) ?? null
    : null;

  const doAssign = async (c: Candidate) => {
    setAssigningId(c.resource_id);
    try {
      const res = await api.assignVisit(visit.id, c.resource_id);
      await onRefresh();
      setReassigning(false);
      setNotify({ resourceId: c.resource_id, name: c.name, message: defaultMessage(visit, c.name) });
      if (res.warnings.length) toast.warning(res.warnings.join("; "));
      else toast.success(`Assigned ${c.name}`);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setAssigningId(null);
    }
  };

  const doCallOut = async () => {
    setBusy(true);
    try {
      const res = await api.callOutVisit(visit.id);
      await onRefresh();
      setConfirm(null);
      onSelectVisit(res.replacement_schedule_id); // follow to the open replacement
      toast.success("Call-out recorded — replacement shift opened");
    } catch (e) {
      toast.error(String(e));
    } finally {
      setBusy(false);
    }
  };

  const doCancel = async () => {
    setBusy(true);
    try {
      await api.cancelVisit(visit.id);
      await onRefresh();
      setConfirm(null);
      onClose(); // cancelled visits drop off the board
      toast.success("Visit cancelled");
    } catch (e) {
      toast.error(String(e));
      setBusy(false);
    }
  };

  const doOutcome = async (outcome: "completed" | "no_show") => {
    setBusy(true);
    try {
      await api.patchVisit(visit.id, { status: outcome });
      await onRefresh();
      toast.success(outcome === "completed" ? "Marked completed" : "Marked no-show");
    } catch (e) {
      toast.error(String(e));
    } finally {
      setBusy(false);
    }
  };

  const queueText = async () => {
    if (!notify) return;
    setBusy(true);
    try {
      const res = await api.notifyCaregiver(visit.id, notify.resourceId, notify.message);
      setQueuedTaskId(res.task_id ?? "");
      toast.success("Text queued for approval");
    } catch (e) {
      toast.error(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="absolute right-0 top-0 flex h-full w-full max-w-md flex-col border-l bg-card shadow-xl">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b p-4">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold">{visit.client_name}</h2>
            <p className="mt-0.5 text-sm text-muted-foreground">
              {formatDayTime(visit.start_time)} · {formatRange(visit)}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant={meta.badge}>{meta.label}</Badge>
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
          {/* Facts */}
          <div className="space-y-3 text-sm">
            <Field label="Caregiver">
              {visit.resource_name ?? <span className="text-muted-foreground">Unassigned</span>}
            </Field>
            {visit.required_qualification_names.length > 0 && (
              <Field label="Required qualifications">
                <div className="flex flex-wrap gap-1">
                  {visit.required_qualification_names.map((q) => (
                    <Badge key={q} variant="outline">
                      {q}
                    </Badge>
                  ))}
                </div>
              </Field>
            )}
            {visit.notes && <Field label="Notes">{visit.notes}</Field>}
          </div>

          {/* Related-visit links */}
          {original && (
            <RelatedLink label="Covers a call-out" onClick={() => onSelectVisit(original.id)}>
              View the original visit
            </RelatedLink>
          )}
          {visit.status === "called_out" && replacement && (
            <RelatedLink
              label="Replacement opened"
              onClick={() => onSelectVisit(replacement.id)}
            >
              View the open replacement shift
            </RelatedLink>
          )}

          {/* Notify prompt (after an assign) */}
          {notify && (
            <div className="rounded-lg border bg-muted/30 p-3">
              {queuedTaskId !== null ? (
                <div className="space-y-2">
                  <Badge variant="warning" className="gap-1">
                    <MessageSquarePlus className="h-3 w-3" /> Text queued for approval
                  </Badge>
                  <p className="text-xs text-muted-foreground">
                    Approve it in{" "}
                    <Link to="/tasks" className="underline hover:text-foreground">
                      Tasks
                    </Link>{" "}
                    to send.
                  </p>
                  <Button size="sm" variant="ghost" onClick={() => setNotify(null)}>
                    <Check className="h-4 w-4" /> Done
                  </Button>
                </div>
              ) : (
                <div className="space-y-2">
                  <p className="text-sm font-medium">Text {firstName(notify.name)} about this shift?</p>
                  <Textarea
                    value={notify.message}
                    onChange={(e) => setNotify({ ...notify, message: e.target.value })}
                    rows={3}
                  />
                  <div className="flex justify-end gap-2">
                    <Button size="sm" variant="ghost" onClick={() => setNotify(null)} disabled={busy}>
                      Skip
                    </Button>
                    <Button size="sm" onClick={queueText} disabled={busy || !notify.message.trim()}>
                      Queue text
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Actions by state (hidden while the notify prompt is up) */}
          {!notify && (
            <div className="space-y-3">
              {visit.status === "open" && (
                <Section title="Assign a caregiver">
                  <CandidateList
                    candidates={candidates}
                    loading={candLoading}
                    assigningId={assigningId}
                    onAssign={doAssign}
                  />
                </Section>
              )}

              {visit.status === "scheduled" && reassigning && (
                <Section title="Reassign to">
                  <CandidateList
                    candidates={candidates}
                    loading={candLoading}
                    assigningId={assigningId}
                    onAssign={doAssign}
                  />
                  <Button
                    size="sm"
                    variant="ghost"
                    className="mt-2"
                    onClick={() => setReassigning(false)}
                  >
                    Cancel reassign
                  </Button>
                </Section>
              )}

              {visit.status === "scheduled" && !reassigning && !isPast && (
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" onClick={() => setConfirm("callout")}>
                    Call out
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setReassigning(true)}>
                    Reassign
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-destructive hover:text-destructive"
                    onClick={() => setConfirm("cancel")}
                  >
                    Cancel visit
                  </Button>
                </div>
              )}

              {visit.status === "scheduled" && !reassigning && isPast && (
                <Section title="Record the outcome">
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => doOutcome("completed")} disabled={busy}>
                      Mark completed
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => doOutcome("no_show")}
                      disabled={busy}
                    >
                      No-show
                    </Button>
                  </div>
                </Section>
              )}
            </div>
          )}

          {/* Technical detail */}
          <div className="border-t pt-3">
            <button
              onClick={() => setShowTech((s) => !s)}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              {showTech ? "Hide" : "Show"} technical detail
            </button>
            {showTech && (
              <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-2 text-[11px] leading-relaxed">
                {JSON.stringify(visit, null, 2)}
              </pre>
            )}
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={confirm === "callout"}
        title="Record a call-out?"
        body={`This marks ${visit.resource_name ?? "the caregiver"}'s visit with ${visit.client_name} as called out and opens a replacement shift to fill.`}
        confirmLabel="Record call-out"
        onConfirm={doCallOut}
        onClose={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === "cancel"}
        title="Cancel this visit?"
        body={`This cancels ${visit.client_name}'s visit on ${formatDayTime(visit.start_time)}. This can't be undone.`}
        confirmLabel="Cancel visit"
        destructive
        onConfirm={doCancel}
        onClose={() => setConfirm(null)}
      />
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-0.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div>{children}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-2 text-xs font-semibold text-muted-foreground">{title}</div>
      {children}
    </div>
  );
}

function RelatedLink({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className="flex w-full items-center justify-between rounded-md border bg-muted/30 px-3 py-2 text-left text-sm transition-colors hover:border-primary/40"
    >
      <span>
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        <span className="mt-0.5 block">{children}</span>
      </span>
      <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
    </button>
  );
}
