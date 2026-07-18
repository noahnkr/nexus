import { useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ApplicantCreate, ApplicantFacets } from "@/lib/api";

// Manual applicant entry — name required, the rest optional. Qualifications and
// regions are multi-select checkboxes drawn from the facets endpoint so they always
// match the tenant's reference data. New applicants always start at the "Applied"
// stage (server-enforced), so there's no stage picker.
function CheckList({
  title,
  options,
  selected,
  onToggle,
}: {
  title: string;
  options: { id: string; name: string }[];
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  if (options.length === 0) return null;
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-muted-foreground">
        {title}
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

export function ApplicantCreateDialog({
  open,
  onClose,
  onCreate,
  facets,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (body: ApplicantCreate) => Promise<void>;
  facets: ApplicantFacets;
}) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [source, setSource] = useState("");
  const [quals, setQuals] = useState<Set<string>>(new Set());
  const [regions, setRegions] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  const reset = () => {
    setName("");
    setPhone("");
    setEmail("");
    setSource("");
    setQuals(new Set());
    setRegions(new Set());
  };

  const toggle = (setter: React.Dispatch<React.SetStateAction<Set<string>>>) => (id: string) =>
    setter((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const submit = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await onCreate({
        name: name.trim(),
        phone: phone.trim() || null,
        email: email.trim() || null,
        source: source.trim() || null,
        qualification_ids: [...quals],
        region_ids: [...regions],
      });
      reset();
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
          <h2 className="text-base font-semibold">New applicant</h2>
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
              placeholder="Who is the applicant?"
            />
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Phone
              </label>
              <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+1…" />
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Email
              </label>
              <Input
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="name@example.com"
              />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Source
            </label>
            <Input
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="indeed, referral…"
              list="applicant-sources"
            />
            <datalist id="applicant-sources">
              {facets.sources.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
          </div>
          <CheckList
            title="Qualifications"
            options={facets.qualifications}
            selected={quals}
            onToggle={toggle(setQuals)}
          />
          <CheckList
            title="Regions"
            options={facets.regions}
            selected={regions}
            onToggle={toggle(setRegions)}
          />
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || !name.trim()}>
            Create applicant
          </Button>
        </div>
      </div>
    </div>
  );
}
