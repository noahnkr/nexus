import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Plus, X } from "lucide-react";
import {
  api,
  type Availability,
  type Credential,
  type QualificationRef,
  type ResourceStatus,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { TimePicker } from "@/components/ui/TimePicker";
import { CredentialsEditor } from "@/components/caregivers/CredentialsEditor";
import { resourceStatusMeta } from "@/lib/workforce";

// The ONE caregiver-editing surface, shared by the schedule board and the M18b
// Roster tab — deliberately not forked, so a credential added from the board and
// one added from the roster leave the identical audit trail. Edits
// contact/address/zip, the languages/traits tags matching feeds on, and per-day
// availability in the existing {"mon":["08:00-16:00"]} shape. Save →
// patchRosterMember, which emits one resource.updated naming the changed fields.
//
// M18b adds two sections that write on their OWN, immediately (not on Save):
//   * Credentials — dated rows via the workforce REST routes.
//   * Active / inactive — the roster PATCH, which emits resource.status_changed.
// Both are discrete, consequential acts (deactivating pulls someone off the board
// and out of matching), so batching them behind "Save changes" would hide them.
//
// Credentials and status are FETCHED here rather than taken as props: the board's
// payload has no credentials, and making the drawer self-sufficient is what keeps
// it one component instead of two.
const DAYS: { key: string; label: string }[] = [
  { key: "mon", label: "Mon" },
  { key: "tue", label: "Tue" },
  { key: "wed", label: "Wed" },
  { key: "thu", label: "Thu" },
  { key: "fri", label: "Fri" },
  { key: "sat", label: "Sat" },
  { key: "sun", label: "Sun" },
];

interface Range {
  start: string;
  end: string;
}
type DayRanges = Record<string, Range[]>;

function parseAvailability(a: Availability): DayRanges {
  const out: DayRanges = {};
  for (const { key } of DAYS) {
    out[key] = (a[key] ?? []).map((r) => {
      const [start, end] = String(r).split("-");
      return { start: start ?? "", end: end ?? "" };
    });
  }
  return out;
}

function serializeAvailability(dr: DayRanges): Availability {
  const out: Availability = {};
  for (const { key } of DAYS) {
    const ranges = (dr[key] ?? [])
      .filter((r) => r.start && r.end)
      .map((r) => `${r.start}-${r.end}`);
    if (ranges.length) out[key] = ranges;
  }
  return out;
}

function TagInput({
  label,
  tags,
  onChange,
  placeholder,
}: {
  label: string;
  tags: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
}) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const t = draft.trim();
    if (t && !tags.includes(t)) onChange([...tags, t]);
    setDraft("");
  };
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-muted-foreground">{label}</label>
      {tags.length > 0 && (
        <div className="mb-1.5 flex flex-wrap gap-1.5">
          {tags.map((t) => (
            <span
              key={t}
              className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-xs text-primary"
            >
              {t}
              <button
                type="button"
                onClick={() => onChange(tags.filter((x) => x !== t))}
                aria-label={`Remove ${t}`}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          placeholder={placeholder}
        />
        <Button type="button" variant="outline" size="sm" onClick={add} disabled={!draft.trim()}>
          Add
        </Button>
      </div>
    </div>
  );
}

// The fields the drawer edits — satisfied structurally by both `CaregiverRoster`
// (the board's payload) and `RosterCaregiver` (the Roster tab's), so neither
// caller has to reshape its rows.
export interface DrawerCaregiver {
  id: string;
  name: string;
  phone: string | null;
  email: string | null;
  address: string | null;
  zip: string | null;
  languages: string[];
  traits: string[];
  availability: Availability;
  status: ResourceStatus;
  hours_this_week: number;
}

export function CaregiverDrawer({
  caregiver,
  onClose,
  onSaved,
}: {
  caregiver: DrawerCaregiver;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [phone, setPhone] = useState(caregiver.phone ?? "");
  const [email, setEmail] = useState(caregiver.email ?? "");
  const [address, setAddress] = useState(caregiver.address ?? "");
  const [zip, setZip] = useState(caregiver.zip ?? "");
  const [languages, setLanguages] = useState<string[]>(caregiver.languages);
  const [traits, setTraits] = useState<string[]>(caregiver.traits);
  const [avail, setAvail] = useState<DayRanges>(parseAvailability(caregiver.availability));
  const [busy, setBusy] = useState(false);

  // Compliance state, owned by the drawer (see the header note).
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [status, setStatus] = useState<ResourceStatus>(caregiver.status);
  const [qualifications, setQualifications] = useState<QualificationRef[]>([]);
  const [confirmDeactivate, setConfirmDeactivate] = useState(false);
  const [togglingStatus, setTogglingStatus] = useState(false);

  const loadCompliance = useCallback(async () => {
    const roster = await api.getWorkforceRoster();
    const row = roster.caregivers.find((c) => c.id === caregiver.id);
    setCredentials(row?.credentials ?? []);
    if (row) setStatus(row.status);
  }, [caregiver.id]);

  useEffect(() => {
    loadCompliance().catch(() => {});
    // The tenant's qualification vocabulary — the same list the applicant
    // multi-selects use, so there is one source for credential names.
    api
      .getApplicantFacets()
      .then((f) => setQualifications(f.qualifications))
      .catch(() => {});
  }, [loadCompliance]);

  // A credential write changes the roster's compliance counts, so the parent
  // refetches too (the board ignores the extra call; the Roster tab needs it).
  const onCredentialsChanged = async () => {
    await loadCompliance();
    await onSaved();
  };

  const setDay = (key: string, ranges: Range[]) =>
    setAvail((prev) => ({ ...prev, [key]: ranges }));

  const applyStatus = async (next: ResourceStatus) => {
    setTogglingStatus(true);
    try {
      await api.patchRosterMember(caregiver.id, { status: next });
      setStatus(next);
      await onSaved();
      toast.success(
        next === "inactive" ? "Caregiver deactivated" : "Caregiver reactivated",
      );
    } catch (e) {
      toast.error(String(e));
    } finally {
      setTogglingStatus(false);
      setConfirmDeactivate(false);
    }
  };

  const save = async () => {
    setBusy(true);
    try {
      await api.patchRosterMember(caregiver.id, {
        phone: phone.trim() || null,
        email: email.trim() || null,
        address: address.trim() || null,
        zip: zip.trim() || null,
        languages,
        traits,
        availability: serializeAvailability(avail),
      });
      await onSaved();
      toast.success("Caregiver updated");
      onClose();
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
        <div className="flex items-start justify-between gap-3 border-b p-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-base font-semibold">{caregiver.name}</h2>
              {status === "inactive" && (
                <Badge variant={resourceStatusMeta(status).badge}>
                  {resourceStatusMeta(status).label}
                </Badge>
              )}
            </div>
            <p className="mt-0.5 text-sm text-muted-foreground tabular-nums">
              {caregiver.hours_this_week}h scheduled this week
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Phone</label>
              <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+1…" />
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Email</label>
              <Input value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
          </div>
          <div className="flex gap-3">
            <div className="flex-[2]">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Address</label>
              <Input value={address} onChange={(e) => setAddress(e.target.value)} />
            </div>
            <div className="w-28">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">ZIP</label>
              <Input value={zip} onChange={(e) => setZip(e.target.value)} />
            </div>
          </div>

          <TagInput
            label="Languages"
            tags={languages}
            onChange={setLanguages}
            placeholder="en, es, tl…"
          />
          <TagInput
            label="Traits"
            tags={traits}
            onChange={setTraits}
            placeholder="female caregiver, speaks spanish…"
          />

          <div>
            <label className="mb-1.5 block text-xs font-medium text-muted-foreground">
              Weekly availability
            </label>
            <div className="space-y-1.5">
              {DAYS.map(({ key, label }) => {
                const ranges = avail[key] ?? [];
                return (
                  <div key={key} className="flex items-start gap-2">
                    <span className="w-9 shrink-0 pt-2 text-xs font-medium text-muted-foreground">
                      {label}
                    </span>
                    <div className="flex-1 space-y-1.5">
                      {ranges.map((r, i) => (
                        <div key={i} className="flex items-center gap-1.5">
                          <div className="w-32">
                            <TimePicker
                              value={r.start}
                              onChange={(v) =>
                                setDay(
                                  key,
                                  ranges.map((x, j) => (j === i ? { ...x, start: v } : x)),
                                )
                              }
                            />
                          </div>
                          <span className="text-xs text-muted-foreground">–</span>
                          <div className="w-32">
                            <TimePicker
                              value={r.end}
                              align="end"
                              onChange={(v) =>
                                setDay(
                                  key,
                                  ranges.map((x, j) => (j === i ? { ...x, end: v } : x)),
                                )
                              }
                            />
                          </div>
                          <button
                            type="button"
                            onClick={() => setDay(key, ranges.filter((_, j) => j !== i))}
                            className="text-muted-foreground hover:text-destructive"
                            aria-label="Remove range"
                          >
                            <X className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      ))}
                      <button
                        type="button"
                        onClick={() => setDay(key, [...ranges, { start: "09:00", end: "17:00" }])}
                        className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
                      >
                        <Plus className="h-3 w-3" /> Add hours
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* --- Credentials: writes immediately, not on Save --- */}
          <CredentialsEditor
            resourceId={caregiver.id}
            credentials={credentials}
            qualifications={qualifications}
            onChanged={onCredentialsChanged}
          />

          {/* --- Active / inactive: also immediate. Deactivating is confirmed
              because it silently removes someone from staffing surfaces. --- */}
          <div className="rounded-lg border p-3">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-xs font-medium text-muted-foreground">Roster status</div>
                <div className="mt-0.5 text-sm">
                  {resourceStatusMeta(status).label}
                </div>
              </div>
              {status === "active" ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setConfirmDeactivate(true)}
                  disabled={togglingStatus || confirmDeactivate}
                >
                  Deactivate
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => applyStatus("active")}
                  disabled={togglingStatus}
                >
                  {togglingStatus ? "Working…" : "Reactivate"}
                </Button>
              )}
            </div>
            {confirmDeactivate && (
              <div className="mt-2.5 space-y-2 border-t pt-2.5">
                <p className="text-xs text-muted-foreground">
                  They'll be removed from the schedule board and from caregiver
                  matching. Their visit history is kept, and you can reactivate them
                  here at any time.
                </p>
                <div className="flex justify-end gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setConfirmDeactivate(false)}
                    disabled={togglingStatus}
                  >
                    Cancel
                  </Button>
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() => applyStatus("inactive")}
                    disabled={togglingStatus}
                  >
                    {togglingStatus ? "Working…" : "Deactivate"}
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t p-4">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={save} disabled={busy}>
            {busy ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </div>
    </div>
  );
}
