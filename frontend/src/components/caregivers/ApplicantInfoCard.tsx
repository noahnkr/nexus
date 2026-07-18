import { useState } from "react";
import { ChevronDown, ChevronRight, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/automations/ConfirmDialog";
import { CAREGIVER_STAGES } from "@/lib/caregivers";
import type { Applicant, ApplicantFacets, ApplicantPatch, ApplicantStage } from "@/lib/api";

const selectClass =
  "h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-sm">{value?.trim() ? value : "—"}</p>
    </div>
  );
}

function ChipToggle({
  options,
  selected,
  onToggle,
}: {
  options: { id: string; name: string }[];
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  return (
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
  );
}

// The applicant's core record: basic fields (view / inline-edit), quals/regions
// checkboxes, notes, the stage selector (with a hire-confirm dialog), and a
// read-only availability expander. Edits PATCH only the changed fields via onPatch;
// a stage change routes through the same onPatch ({stage}), so the server emits
// applicant.updated / applicant.stage_changed exactly as appropriate — and, on
// hire, promotes to a caregiver record.
export function ApplicantInfoCard({
  applicant,
  facets,
  onPatch,
  busy,
}: {
  applicant: Applicant;
  facets: ApplicantFacets;
  onPatch: (patch: ApplicantPatch) => Promise<void>;
  busy: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(applicant.name);
  const [phone, setPhone] = useState(applicant.phone ?? "");
  const [email, setEmail] = useState(applicant.email ?? "");
  const [source, setSource] = useState(applicant.source ?? "");
  const [notes, setNotes] = useState(applicant.notes ?? "");
  const [quals, setQuals] = useState<Set<string>>(new Set(applicant.qualification_ids));
  const [regions, setRegions] = useState<Set<string>>(new Set(applicant.region_ids));
  const [showAvail, setShowAvail] = useState(false);
  const [pendingHire, setPendingHire] = useState(false);

  const startEdit = () => {
    setName(applicant.name);
    setPhone(applicant.phone ?? "");
    setEmail(applicant.email ?? "");
    setSource(applicant.source ?? "");
    setNotes(applicant.notes ?? "");
    setQuals(new Set(applicant.qualification_ids));
    setRegions(new Set(applicant.region_ids));
    setEditing(true);
  };

  const toggle = (setter: React.Dispatch<React.SetStateAction<Set<string>>>) => (id: string) =>
    setter((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const sameSet = (a: Set<string>, b: string[]) =>
    a.size === b.length && b.every((x) => a.has(x));

  const save = async () => {
    const patch: ApplicantPatch = {};
    if (name.trim() && name.trim() !== applicant.name) patch.name = name.trim();
    if ((phone.trim() || null) !== applicant.phone) patch.phone = phone.trim() || null;
    if ((email.trim() || null) !== applicant.email) patch.email = email.trim() || null;
    if ((source.trim() || null) !== applicant.source) patch.source = source.trim() || null;
    if ((notes.trim() || null) !== applicant.notes) patch.notes = notes.trim() || null;
    if (!sameSet(quals, applicant.qualification_ids)) patch.qualification_ids = [...quals];
    if (!sameSet(regions, applicant.region_ids)) patch.region_ids = [...regions];
    if (Object.keys(patch).length > 0) await onPatch(patch);
    setEditing(false);
  };

  const onStageChange = (stage: ApplicantStage) => {
    if (stage === applicant.stage) return;
    if (stage === "hired") setPendingHire(true);
    else void onPatch({ stage });
  };

  const hasAvailability = Object.keys(applicant.availability ?? {}).length > 0;

  return (
    <Card>
      <CardContent className="space-y-4 p-4">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Details
          </p>
          {!editing && (
            <button
              onClick={startEdit}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              <Pencil className="h-3.5 w-3.5" /> Edit
            </button>
          )}
        </div>

        {editing ? (
          <div className="space-y-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Name</label>
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Phone</label>
                <Input value={phone} onChange={(e) => setPhone(e.target.value)} />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Email</label>
                <Input value={email} onChange={(e) => setEmail(e.target.value)} />
              </div>
              <div className="col-span-2">
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Source</label>
                <Input value={source} onChange={(e) => setSource(e.target.value)} list="applicant-src-edit" />
                <datalist id="applicant-src-edit">
                  {facets.sources.map((s) => (
                    <option key={s} value={s} />
                  ))}
                </datalist>
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Qualifications</label>
              <ChipToggle options={facets.qualifications} selected={quals} onToggle={toggle(setQuals)} />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Regions</label>
              <ChipToggle options={facets.regions} selected={regions} onToggle={toggle(setRegions)} />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Notes</label>
              <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setEditing(false)} disabled={busy}>
                Cancel
              </Button>
              <Button size="sm" onClick={save} disabled={busy}>
                Save
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Name" value={applicant.name} />
              <Field label="Phone" value={applicant.phone} />
              <Field label="Email" value={applicant.email} />
              <Field label="Source" value={applicant.source} />
              <Field
                label="Qualifications"
                value={applicant.qualification_names.join(", ") || null}
              />
              <Field label="Regions" value={applicant.region_names.join(", ") || null} />
            </div>
            {applicant.notes?.trim() && (
              <div>
                <p className="text-xs font-medium text-muted-foreground">Notes</p>
                <p className="mt-0.5 whitespace-pre-wrap text-sm">{applicant.notes}</p>
              </div>
            )}
          </>
        )}

        <div className="border-t pt-4">
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">Stage</p>
          <select
            className={selectClass}
            value={applicant.stage}
            disabled={busy}
            onChange={(e) => onStageChange(e.target.value as ApplicantStage)}
          >
            {CAREGIVER_STAGES.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </div>

        {hasAvailability && (
          <div className="border-t pt-3">
            <button
              onClick={() => setShowAvail((v) => !v)}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              {showAvail ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
              Availability
            </button>
            {showAvail && (
              <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-2.5 text-xs text-muted-foreground">
                {JSON.stringify(applicant.availability, null, 2)}
              </pre>
            )}
          </div>
        )}
      </CardContent>

      <ConfirmDialog
        open={pendingHire}
        title="Hire this applicant?"
        body={`This creates a caregiver record for ${applicant.name} and marks them hired. Continue?`}
        confirmLabel="Hire"
        onConfirm={async () => {
          setPendingHire(false);
          await onPatch({ stage: "hired" });
        }}
        onClose={() => setPendingHire(false)}
      />
    </Card>
  );
}
