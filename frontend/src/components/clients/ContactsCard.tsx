import { useState } from "react";
import { toast } from "sonner";
import { Mail, Pencil, Phone, Plus, Star, Trash2, X } from "lucide-react";
import { api, type ClientContact, type ClientContactCreate } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/automations/ConfirmDialog";

// Add/edit dialog for one family contact. Name required; relationship, phone,
// email, and "primary" toggle optional. At most one primary — the server clears
// the others in the same transaction.
function ContactDialog({
  initial,
  onClose,
  onSave,
}: {
  initial: ClientContact | null;
  onClose: () => void;
  onSave: (body: ClientContactCreate) => Promise<void>;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [relationship, setRelationship] = useState(initial?.relationship ?? "");
  const [phone, setPhone] = useState(initial?.phone ?? "");
  const [email, setEmail] = useState(initial?.email ?? "");
  const [isPrimary, setIsPrimary] = useState(initial?.is_primary ?? false);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await onSave({
        name: name.trim(),
        relationship: relationship.trim() || null,
        phone: phone.trim() || null,
        email: email.trim() || null,
        is_primary: isPrimary,
      });
      onClose();
    } catch {
      // toast handled by caller
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
        className="w-full max-w-md rounded-lg border bg-card p-5 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">{initial ? "Edit contact" : "Add contact"}</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-3">
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Name</label>
              <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Relationship</label>
              <Input
                value={relationship}
                onChange={(e) => setRelationship(e.target.value)}
                placeholder="daughter, POA…"
              />
            </div>
          </div>
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
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={isPrimary}
              onChange={(e) => setIsPrimary(e.target.checked)}
              className="h-4 w-4 rounded border-input"
            />
            Primary contact
          </label>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || !name.trim()}>
            {initial ? "Save" : "Add contact"}
          </Button>
        </div>
      </div>
    </div>
  );
}

export function ContactsCard({
  clientId,
  contacts,
  onChanged,
}: {
  clientId: string;
  contacts: ClientContact[];
  onChanged: () => Promise<void>;
}) {
  const [dialog, setDialog] = useState<{ mode: "add" } | { mode: "edit"; contact: ClientContact } | null>(
    null,
  );
  const [deleting, setDeleting] = useState<ClientContact | null>(null);

  const onSave = async (body: ClientContactCreate) => {
    try {
      if (dialog?.mode === "edit") {
        await api.patchContact(clientId, dialog.contact.id, body);
        toast.success("Contact updated");
      } else {
        await api.createContact(clientId, body);
        toast.success("Contact added");
      }
      await onChanged();
    } catch (e) {
      toast.error(String(e));
      throw e;
    }
  };

  const onDelete = async () => {
    if (!deleting) return;
    try {
      await api.deleteContact(clientId, deleting.id);
      setDeleting(null);
      await onChanged();
      toast.success("Contact removed");
    } catch (e) {
      toast.error(String(e));
    }
  };

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Family contacts
          </p>
          <button
            onClick={() => setDialog({ mode: "add" })}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <Plus className="h-3.5 w-3.5" /> Add
          </button>
        </div>

        {contacts.length === 0 ? (
          <p className="text-sm text-muted-foreground">No family contacts recorded yet.</p>
        ) : (
          <ul className="divide-y">
            {contacts.map((c) => (
              <li key={c.id} className="flex items-start justify-between gap-3 py-2.5 first:pt-0">
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5">
                    {c.is_primary && (
                      <Star className="h-3.5 w-3.5 shrink-0 fill-warning text-warning" />
                    )}
                    <span className="truncate text-sm font-medium">{c.name}</span>
                    {c.relationship && (
                      <span className="text-xs text-muted-foreground">· {c.relationship}</span>
                    )}
                  </div>
                  <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                    {c.phone && (
                      <span className="inline-flex items-center gap-1">
                        <Phone className="h-3 w-3" /> {c.phone}
                      </span>
                    )}
                    {c.email && (
                      <span className="inline-flex items-center gap-1">
                        <Mail className="h-3 w-3" /> {c.email}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    onClick={() => setDialog({ mode: "edit", contact: c })}
                    className="text-muted-foreground hover:text-foreground"
                    aria-label={`Edit ${c.name}`}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={() => setDeleting(c)}
                    className="text-muted-foreground hover:text-destructive"
                    aria-label={`Remove ${c.name}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>

      {dialog && (
        <ContactDialog
          initial={dialog.mode === "edit" ? dialog.contact : null}
          onClose={() => setDialog(null)}
          onSave={onSave}
        />
      )}
      <ConfirmDialog
        open={deleting !== null}
        title="Remove this contact?"
        body={`This removes ${deleting?.name ?? "the contact"} from this client's family contacts.`}
        confirmLabel="Remove"
        destructive
        onConfirm={onDelete}
        onClose={() => setDeleting(null)}
      />
    </Card>
  );
}
