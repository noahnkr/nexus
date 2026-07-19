import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, type SelectOption } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/textarea";
import { PARTNER_CATEGORIES, categoryMeta } from "@/lib/referrals";
import type { PartnerCategory } from "@/lib/api";

// The shape both create (Track / New partner) and edit (from the drawer) submit.
export interface PartnerForm {
  name: string;
  category: PartnerCategory | null;
  contact_name: string | null;
  phone: string | null;
  email: string | null;
  notes: string | null;
}

const CATEGORY_OPTIONS: SelectOption<PartnerCategory>[] = PARTNER_CATEGORIES.map((c) => ({
  value: c,
  label: categoryMeta(c).label,
  dot: categoryMeta(c).dot,
}));

// Create/edit dialog shared by the Track button, the New partner button, and the
// drawer's Edit. `initial` prefills it — Track passes just the source name, edit
// passes the whole partner. Name is required; everything else is optional (a rename
// simply re-joins by the new name, by design).
export function PartnerDialog({
  open,
  title,
  submitLabel,
  initial,
  onSubmit,
  onClose,
}: {
  open: boolean;
  title: string;
  submitLabel: string;
  initial?: Partial<PartnerForm>;
  onSubmit: (body: PartnerForm) => Promise<void>;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [category, setCategory] = useState<PartnerCategory | "">("");
  const [contactName, setContactName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);

  // Sync the form to `initial` each time the dialog opens.
  useEffect(() => {
    if (!open) return;
    setName(initial?.name ?? "");
    setCategory((initial?.category ?? "") as PartnerCategory | "");
    setContactName(initial?.contact_name ?? "");
    setPhone(initial?.phone ?? "");
    setEmail(initial?.email ?? "");
    setNotes(initial?.notes ?? "");
    setBusy(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  const submit = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await onSubmit({
        name: name.trim(),
        category: category || null,
        contact_name: contactName.trim() || null,
        phone: phone.trim() || null,
        email: email.trim() || null,
        notes: notes.trim() || null,
      });
      onClose();
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
          <h2 className="text-base font-semibold">{title}</h2>
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
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Name
            </label>
            <Input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. St. Mary's Hospital"
            />
            <p className="mt-1 text-[11px] text-muted-foreground">
              Must match the lead source string exactly to enrich its leads.
            </p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Category
            </label>
            <Select
              value={category}
              onChange={(v) => setCategory(v)}
              options={CATEGORY_OPTIONS}
              clearable
              placeholder="Untyped"
              aria-label="Category"
            />
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Contact name
              </label>
              <Input
                value={contactName}
                onChange={(e) => setContactName(e.target.value)}
                placeholder="Who do you talk to?"
              />
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Phone
              </label>
              <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+1…" />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Email
            </label>
            <Input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@example.com"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Notes
            </label>
            <Textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              placeholder="Relationship notes, referral patterns…"
            />
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || !name.trim()}>
            {submitLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
