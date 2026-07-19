import { useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, type SelectOption } from "@/components/ui/Select";
import { PAYERS, PAYER_LABELS } from "@/lib/clients";
import type { ClientCreate, ClientFacets, Payer } from "@/lib/api";

const PAYER_OPTIONS: SelectOption<Payer>[] = PAYERS.map((p) => ({
  value: p,
  label: PAYER_LABELS[p],
}));

// Manual client entry — name required, the rest optional. New clients always start
// `active` (server default), so there's no status picker. Region + payer use the
// shared Select; authorized hours is a plain number input.
export function ClientCreateDialog({
  open,
  onClose,
  onCreate,
  facets,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (body: ClientCreate) => Promise<void>;
  facets: ClientFacets;
}) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [payer, setPayer] = useState<Payer | "">("");
  const [regionId, setRegionId] = useState("");
  const [hours, setHours] = useState("");
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  const reset = () => {
    setName("");
    setPhone("");
    setEmail("");
    setPayer("");
    setRegionId("");
    setHours("");
  };

  const submit = async () => {
    if (!name.trim()) return;
    const parsedHours = hours.trim() === "" ? null : Number(hours);
    if (parsedHours != null && (Number.isNaN(parsedHours) || parsedHours < 0)) return;
    setBusy(true);
    try {
      await onCreate({
        name: name.trim(),
        phone: phone.trim() || null,
        email: email.trim() || null,
        payer: payer || null,
        region_id: regionId || null,
        authorized_hours_per_week: parsedHours,
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
          <h2 className="text-base font-semibold">New client</h2>
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
              placeholder="Who is the client?"
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
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Payer
              </label>
              <Select
                value={payer}
                onChange={(v) => setPayer(v)}
                options={PAYER_OPTIONS}
                clearable
                placeholder="Unknown"
                aria-label="Payer"
              />
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
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Authorized hours / week
            </label>
            <Input
              type="number"
              min={0}
              step="0.5"
              value={hours}
              onChange={(e) => setHours(e.target.value)}
              placeholder="e.g. 20"
              className="w-40"
            />
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || !name.trim()}>
            Create client
          </Button>
        </div>
      </div>
    </div>
  );
}
