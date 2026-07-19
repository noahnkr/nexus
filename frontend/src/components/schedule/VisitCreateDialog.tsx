import { useEffect, useState } from "react";
import { X } from "lucide-react";
import {
  api,
  type CaregiverRoster,
  type ClientRef,
  type QualificationRef,
  type ScheduleCreate,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { hoursBetween } from "@/lib/schedule";

const selectClass =
  "h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

const MAX_REPEAT_WEEKS = 12;

// Multi-select chips (the ApplicantCreateDialog pattern) for required qualifications.
function QualPicker({
  options,
  selected,
  onToggle,
}: {
  options: QualificationRef[];
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  if (options.length === 0) return null;
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-muted-foreground">
        Required qualifications
      </label>
      <div className="flex flex-wrap gap-1.5">
        {options.map((o) => {
          const on = selected.has(o.id);
          return (
            <button
              key={o.id}
              type="button"
              onClick={() => onToggle(o.id)}
              className={
                "rounded-full border px-2.5 py-1 text-xs transition-colors " +
                (on
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-input text-muted-foreground hover:border-primary/40 hover:text-foreground")
              }
            >
              {o.name}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// New-visit form. A caregiver is optional — leaving it blank creates an unfilled
// open shift. "Repeat weekly until" expands the series server-side (<=12 extra
// visits), mirrored with a client-side cap so the error is caught before submit.
export function VisitCreateDialog({
  open,
  onClose,
  onCreate,
  clients,
  caregivers,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (body: ScheduleCreate) => Promise<void>;
  clients: ClientRef[];
  caregivers: CaregiverRoster[];
}) {
  const [clientId, setClientId] = useState("");
  const [resourceId, setResourceId] = useState("");
  const [date, setDate] = useState("");
  const [start, setStart] = useState("09:00");
  const [end, setEnd] = useState("13:00");
  const [quals, setQuals] = useState<Set<string>>(new Set());
  const [notes, setNotes] = useState("");
  const [repeatUntil, setRepeatUntil] = useState("");
  const [qualOptions, setQualOptions] = useState<QualificationRef[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open && qualOptions.length === 0) {
      api.getApplicantFacets().then((f) => setQualOptions(f.qualifications)).catch(() => {});
    }
  }, [open, qualOptions.length]);

  if (!open) return null;

  const reset = () => {
    setClientId("");
    setResourceId("");
    setDate("");
    setStart("09:00");
    setEnd("13:00");
    setQuals(new Set());
    setNotes("");
    setRepeatUntil("");
    setError(null);
  };

  const toggleQual = (id: string) =>
    setQuals((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const startIso = date && start ? new Date(`${date}T${start}`).toISOString() : null;
  const endIso = date && end ? new Date(`${date}T${end}`).toISOString() : null;
  const hours = startIso && endIso ? hoursBetween(startIso, endIso) : 0;

  const validate = (): string | null => {
    if (!clientId) return "Choose a client.";
    if (!date) return "Choose a date.";
    if (!startIso || !endIso || endIso <= startIso) return "End time must be after start time.";
    if (repeatUntil) {
      const weeks = Math.floor(
        (new Date(repeatUntil).getTime() - new Date(date).getTime()) / (7 * 86_400_000),
      );
      if (weeks < 0) return "The repeat-until date must be on or after the visit date.";
      if (weeks > MAX_REPEAT_WEEKS) return `A weekly series is capped at ${MAX_REPEAT_WEEKS} extra visits.`;
    }
    return null;
  };

  const submit = async () => {
    const problem = validate();
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onCreate({
        client_id: clientId,
        resource_id: resourceId || null,
        start_time: startIso!,
        end_time: endIso!,
        required_qualification_ids: [...quals],
        notes: notes.trim() || null,
        repeat_weekly_until: repeatUntil || null,
      });
      reset();
      onClose();
    } catch (e) {
      // Series conflicts etc. surface the backend's plain message verbatim.
      setError(String(e).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-lg border bg-card p-5 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">New visit</h2>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Client</label>
            <select
              className={selectClass}
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
            >
              <option value="">Choose a client…</option>
              {clients.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Caregiver
            </label>
            <select
              className={selectClass}
              value={resourceId}
              onChange={(e) => setResourceId(e.target.value)}
            >
              <option value="">Leave unassigned → creates an open shift</option>
              {caregivers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>

          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Date</label>
              <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </div>
            <div className="w-24">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Start</label>
              <Input type="time" value={start} onChange={(e) => setStart(e.target.value)} />
            </div>
            <div className="w-24">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">End</label>
              <Input type="time" value={end} onChange={(e) => setEnd(e.target.value)} />
            </div>
          </div>
          {hours > 0 && (
            <p className="text-[11px] text-muted-foreground">{hours}h visit</p>
          )}

          <QualPicker options={qualOptions} selected={quals} onToggle={toggleQual} />

          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Notes</label>
            <Textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
              placeholder="Anything the caregiver should know…"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Repeat weekly until (optional)
            </label>
            <Input
              type="date"
              value={repeatUntil}
              onChange={(e) => setRepeatUntil(e.target.value)}
            />
            <p className="mt-1 text-[11px] text-muted-foreground">
              Creates a weekly series through this date (up to {MAX_REPEAT_WEEKS} extra visits).
            </p>
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || !clientId || !date}>
            {busy ? "Creating…" : "Create visit"}
          </Button>
        </div>
      </div>
    </div>
  );
}
