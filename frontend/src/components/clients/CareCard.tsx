import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, type SelectOption } from "@/components/ui/Select";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/automations/ConfirmDialog";
import { CLIENT_STATUSES, PAYERS, PAYER_LABELS, statusMeta } from "@/lib/clients";
import type { ClientDetail, ClientPatch, ClientStatus, Payer } from "@/lib/api";

const STATUS_OPTIONS: SelectOption<ClientStatus>[] = CLIENT_STATUSES.map((s) => ({
  value: s,
  label: statusMeta(s).label,
  dot: statusMeta(s).dot,
}));

const PAYER_OPTIONS: SelectOption<Payer>[] = PAYERS.map((p) => ({
  value: p,
  label: PAYER_LABELS[p],
}));

// Care details: status (with a discharge-confirm dialog — the hire-confirm
// precedent), payer, region, authorized hours/week, and the free-text care
// summary. Status/payer/region change immediately; hours + summary save together.
export function CareCard({
  client,
  regions,
  onPatch,
  busy,
}: {
  client: ClientDetail;
  regions: { id: string; name: string }[];
  onPatch: (patch: ClientPatch) => Promise<void>;
  busy: boolean;
}) {
  const [hours, setHours] = useState(
    client.authorized_hours_per_week != null ? String(client.authorized_hours_per_week) : "",
  );
  const [summary, setSummary] = useState(client.care_summary ?? "");
  const [pendingDischarge, setPendingDischarge] = useState(false);

  const onStatusChange = (status: ClientStatus) => {
    if (status === client.status) return;
    if (status === "discharged") setPendingDischarge(true);
    else void onPatch({ status });
  };

  const hoursDirty =
    (hours.trim() === "" ? null : Number(hours)) !==
    (client.authorized_hours_per_week ?? null);
  const summaryDirty = (summary.trim() || null) !== (client.care_summary ?? null);
  const dirty = hoursDirty || summaryDirty;

  const saveDetails = async () => {
    const patch: ClientPatch = {};
    if (hoursDirty) {
      const parsed = hours.trim() === "" ? null : Number(hours);
      if (parsed != null && (Number.isNaN(parsed) || parsed < 0)) return;
      patch.authorized_hours_per_week = parsed;
    }
    if (summaryDirty) patch.care_summary = summary.trim() || null;
    if (Object.keys(patch).length > 0) await onPatch(patch);
  };

  return (
    <Card>
      <CardContent className="space-y-4 p-4">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Care
        </p>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Status</label>
            <Select
              value={client.status}
              disabled={busy}
              onChange={onStatusChange}
              options={STATUS_OPTIONS}
              aria-label="Client status"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Payer</label>
            <Select
              value={client.payer ?? ""}
              disabled={busy}
              onChange={(v) => void onPatch({ payer: (v || null) as Payer | null })}
              options={PAYER_OPTIONS}
              clearable
              placeholder="Unknown"
              aria-label="Payer"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Region</label>
            <Select
              value={client.region_id ?? ""}
              disabled={busy}
              onChange={(v) => void onPatch({ region_id: v || null })}
              options={regions.map((r) => ({ value: r.id, label: r.name }))}
              clearable
              placeholder="No region"
              aria-label="Region"
            />
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
              placeholder="—"
            />
          </div>
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Care summary
          </label>
          <Textarea
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            rows={4}
            placeholder="A short note on this client's care needs — condition, mobility, preferences…"
          />
        </div>

        {dirty && (
          <div className="flex justify-end">
            <Button size="sm" onClick={saveDetails} disabled={busy}>
              Save care details
            </Button>
          </div>
        )}
      </CardContent>

      <ConfirmDialog
        open={pendingDischarge}
        title="Discharge this client?"
        body={`This ends service for ${client.name}. Their care history stays, but they'll no longer count toward the active census. Continue?`}
        confirmLabel="Discharge"
        destructive
        onConfirm={async () => {
          setPendingDischarge(false);
          await onPatch({ status: "discharged" });
        }}
        onClose={() => setPendingDischarge(false)}
      />
    </Card>
  );
}
