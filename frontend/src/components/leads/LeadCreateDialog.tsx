import { useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/Select";
import type { LeadCreate, LeadFacets } from "@/lib/api";

// Manual lead entry — name required, the rest optional. Region comes from the
// facets endpoint so the dropdown always matches the tenant's regions. New leads
// always start at the "New" stage (server-enforced), so there's no stage picker.
export function LeadCreateDialog({
  open,
  onClose,
  onCreate,
  facets,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (body: LeadCreate) => Promise<void>;
  facets: LeadFacets;
}) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [source, setSource] = useState("");
  const [regionId, setRegionId] = useState("");
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  const reset = () => {
    setName("");
    setPhone("");
    setEmail("");
    setSource("");
    setRegionId("");
  };

  const submit = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await onCreate({
        name: name.trim(),
        phone: phone.trim() || null,
        email: email.trim() || null,
        source: source.trim() || null,
        region_id: regionId || null,
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
          <h2 className="text-base font-semibold">New lead</h2>
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
              placeholder="Who is the lead?"
            />
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Phone
              </label>
              <Input
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+1…"
              />
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
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Source
              </label>
              <Input
                value={source}
                onChange={(e) => setSource(e.target.value)}
                placeholder="website, referral…"
                list="lead-sources"
              />
              <datalist id="lead-sources">
                {facets.sources.map((s) => (
                  <option key={s} value={s} />
                ))}
              </datalist>
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Region
              </label>
              <Select
                value={regionId}
                onChange={setRegionId}
                options={facets.regions.map((r) => ({ value: r.id, label: r.name }))}
                clearable
                placeholder="No region"
                aria-label="Region"
              />
            </div>
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || !name.trim()}>
            Create lead
          </Button>
        </div>
      </div>
    </div>
  );
}
