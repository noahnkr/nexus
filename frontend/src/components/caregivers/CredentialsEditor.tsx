import { useState } from "react";
import { toast } from "sonner";
import { Plus, Trash2, X } from "lucide-react";
import {
  api,
  type Credential,
  type CredentialCreate,
  type QualificationRef,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { credentialMeta, fmtDate, fmtDaysLeft, sortCredentials } from "@/lib/workforce";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DatePicker } from "@/components/ui/DatePicker";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/Select";

// Dated credentials on one caregiver (M18b). Add / edit dates+notes / delete, all
// through the workforce REST routes — human writes, `source_system='user'`, no
// approval gate (the gate is for agent-initiated outbound effects). The
// (caregiver, qualification) pair is a row's identity, so the qualification is
// picked once at add time and is not editable afterwards; a duplicate pair comes
// back as a 409 and is shown inline rather than as a toast that scrolls away.
//
// Status chips and day counts come straight from the server-derived
// `status`/`days_left` — this file never computes an expiry.

function ExpiryLine({ credential }: { credential: Credential }) {
  const meta = credentialMeta(credential.status);
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
      <Badge variant={meta.badge}>{meta.label}</Badge>
      <span>{fmtDaysLeft(credential.days_left)}</span>
      {credential.issued_at && <span>· issued {fmtDate(credential.issued_at)}</span>}
      {credential.expires_at && <span>· expires {fmtDate(credential.expires_at)}</span>}
    </div>
  );
}

function DateFields({
  issued,
  expires,
  notes,
  setIssued,
  setExpires,
  setNotes,
}: {
  issued: string;
  expires: string;
  notes: string;
  setIssued: (v: string) => void;
  setExpires: (v: string) => void;
  setNotes: (v: string) => void;
}) {
  return (
    <>
      <div className="flex gap-2">
        <div className="flex-1">
          <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
            Issued
          </label>
          <DatePicker value={issued} onChange={setIssued} clearable placeholder="—" />
        </div>
        <div className="flex-1">
          <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
            Expires
          </label>
          {/* Left blank for a credential that doesn't renew — the seam reads a
              null expiry as "no expiry", not as missing data. */}
          <DatePicker
            value={expires}
            onChange={setExpires}
            clearable
            align="end"
            placeholder="No expiry"
          />
        </div>
      </div>
      <Input
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Notes (optional)"
      />
    </>
  );
}

function EditRow({
  credential,
  onDone,
  onCancel,
}: {
  credential: Credential;
  onDone: () => Promise<void>;
  onCancel: () => void;
}) {
  const [issued, setIssued] = useState(credential.issued_at ?? "");
  const [expires, setExpires] = useState(credential.expires_at ?? "");
  const [notes, setNotes] = useState(credential.notes ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.patchCredential(credential.id, {
        issued_at: issued || null,
        expires_at: expires || null,
        notes: notes.trim() || null,
      });
      await onDone();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 rounded-lg border bg-muted/30 p-2.5">
      <div className="text-xs font-medium">{credential.qualification_name}</div>
      <DateFields
        issued={issued}
        expires={expires}
        notes={notes}
        setIssued={setIssued}
        setExpires={setExpires}
        setNotes={setNotes}
      />
      {error && <p className="text-[11px] text-destructive">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button size="sm" onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}

function AddForm({
  resourceId,
  available,
  onDone,
  onCancel,
}: {
  resourceId: string;
  available: QualificationRef[];
  onDone: () => Promise<void>;
  onCancel: () => void;
}) {
  const [qualificationId, setQualificationId] = useState("");
  const [issued, setIssued] = useState("");
  const [expires, setExpires] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const add = async () => {
    if (!qualificationId) return;
    setBusy(true);
    setError(null);
    const body: CredentialCreate = {
      resource_id: resourceId,
      qualification_id: qualificationId,
      issued_at: issued || null,
      expires_at: expires || null,
      notes: notes.trim() || null,
    };
    try {
      await api.createCredential(body);
      await onDone();
      onCancel();
    } catch (e) {
      // A 409 (this caregiver already has this credential) belongs beside the
      // picker that caused it, not in a toast.
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 rounded-lg border bg-muted/30 p-2.5">
      <Select
        value={qualificationId}
        onChange={setQualificationId}
        options={available.map((q) => ({ value: q.id, label: q.name }))}
        searchable
        placeholder="Choose a credential…"
        aria-label="Credential"
      />
      <DateFields
        issued={issued}
        expires={expires}
        notes={notes}
        setIssued={setIssued}
        setExpires={setExpires}
        setNotes={setNotes}
      />
      {error && <p className="text-[11px] text-destructive">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button size="sm" onClick={add} disabled={busy || !qualificationId}>
          {busy ? "Adding…" : "Add credential"}
        </Button>
      </div>
    </div>
  );
}

export function CredentialsEditor({
  resourceId,
  credentials,
  qualifications,
  onChanged,
}: {
  resourceId: string;
  credentials: Credential[];
  qualifications: QualificationRef[];
  onChanged: () => Promise<void>;
}) {
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const held = new Set(credentials.map((c) => c.qualification_id));
  const available = qualifications.filter((q) => !held.has(q.id));
  const sorted = sortCredentials(credentials);

  const remove = async (credential: Credential) => {
    try {
      await api.deleteCredential(credential.id);
      await onChanged();
      toast.success(`${credential.qualification_name} credential removed`);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setConfirmDelete(null);
    }
  };

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <label className="block text-xs font-medium text-muted-foreground">Credentials</label>
        {!adding && available.length > 0 && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <Plus className="h-3 w-3" /> Add credential
          </button>
        )}
      </div>

      <div className="space-y-2">
        {sorted.length === 0 && !adding && (
          <p className="text-xs text-muted-foreground">
            No dated credentials on file. Adding one tracks its expiry on the roster.
          </p>
        )}

        {sorted.map((c) =>
          editingId === c.id ? (
            <EditRow
              key={c.id}
              credential={c}
              onCancel={() => setEditingId(null)}
              onDone={async () => {
                await onChanged();
                setEditingId(null);
              }}
            />
          ) : (
            <div
              key={c.id}
              className={cn(
                "flex items-start justify-between gap-2 rounded-lg border p-2.5",
                c.status === "expired" && "border-destructive/30",
              )}
            >
              <button
                type="button"
                onClick={() => setEditingId(c.id)}
                className="min-w-0 flex-1 text-left"
              >
                <div className="text-sm font-medium">{c.qualification_name}</div>
                <ExpiryLine credential={c} />
                {c.notes && (
                  <p className="mt-1 truncate text-[11px] text-muted-foreground">{c.notes}</p>
                )}
              </button>
              {confirmDelete === c.id ? (
                <div className="flex shrink-0 items-center gap-1">
                  <Button variant="destructive" size="sm" onClick={() => remove(c)}>
                    Remove
                  </Button>
                  <button
                    type="button"
                    onClick={() => setConfirmDelete(null)}
                    className="text-muted-foreground hover:text-foreground"
                    aria-label="Cancel remove"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirmDelete(c.id)}
                  className="shrink-0 text-muted-foreground hover:text-destructive"
                  aria-label={`Remove ${c.qualification_name}`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          ),
        )}

        {adding && (
          <AddForm
            resourceId={resourceId}
            available={available}
            onCancel={() => setAdding(false)}
            onDone={onChanged}
          />
        )}
      </div>
    </div>
  );
}
