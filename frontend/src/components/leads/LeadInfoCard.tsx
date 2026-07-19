import { useState } from "react";
import { ChevronDown, ChevronRight, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/Select";
import { Card, CardContent } from "@/components/ui/card";
import { StageSelect } from "@/components/leads/StageSelect";
import type { Lead, LeadFacets, LeadPatch, LeadStatus } from "@/lib/api";

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-sm">{value?.trim() ? value : "—"}</p>
    </div>
  );
}

// The lead's core record: basic fields (view / inline-edit), the stage selector,
// and a read-only requirements expander. Edits PATCH only the changed fields via
// onPatch; a stage change routes through the same onPatch ({status}), so the
// server emits lead.updated / lead.stage_changed exactly as appropriate.
export function LeadInfoCard({
  lead,
  facets,
  onPatch,
  busy,
}: {
  lead: Lead;
  facets: LeadFacets;
  onPatch: (patch: LeadPatch) => Promise<void>;
  busy: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(lead.name);
  const [phone, setPhone] = useState(lead.phone ?? "");
  const [email, setEmail] = useState(lead.email ?? "");
  const [source, setSource] = useState(lead.source ?? "");
  const [regionId, setRegionId] = useState(lead.region_id ?? "");
  const [showReq, setShowReq] = useState(false);

  const startEdit = () => {
    setName(lead.name);
    setPhone(lead.phone ?? "");
    setEmail(lead.email ?? "");
    setSource(lead.source ?? "");
    setRegionId(lead.region_id ?? "");
    setEditing(true);
  };

  const save = async () => {
    // Send only changed fields (empty text -> null to clear).
    const patch: LeadPatch = {};
    if (name.trim() && name.trim() !== lead.name) patch.name = name.trim();
    if ((phone.trim() || null) !== lead.phone) patch.phone = phone.trim() || null;
    if ((email.trim() || null) !== lead.email) patch.email = email.trim() || null;
    if ((source.trim() || null) !== lead.source) patch.source = source.trim() || null;
    if ((regionId || null) !== lead.region_id) patch.region_id = regionId || null;
    if (Object.keys(patch).length > 0) await onPatch(patch);
    setEditing(false);
  };

  const hasRequirements = Object.keys(lead.requirements ?? {}).length > 0;

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
              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Source</label>
                <Input value={source} onChange={(e) => setSource(e.target.value)} list="lead-src-edit" />
                <datalist id="lead-src-edit">
                  {facets.sources.map((s) => (
                    <option key={s} value={s} />
                  ))}
                </datalist>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Region</label>
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
          <div className="grid grid-cols-2 gap-4">
            <Field label="Name" value={lead.name} />
            <Field label="Phone" value={lead.phone} />
            <Field label="Email" value={lead.email} />
            <Field label="Source" value={lead.source} />
            <Field label="Region" value={lead.region_name} />
          </div>
        )}

        <div className="border-t pt-4">
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">Stage</p>
          <StageSelect
            status={lead.status}
            disabled={busy}
            onChange={(status: LeadStatus) => void onPatch({ status })}
          />
        </div>

        {hasRequirements && (
          <div className="border-t pt-3">
            <button
              onClick={() => setShowReq((v) => !v)}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              {showReq ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
              Requirements
            </button>
            {showReq && (
              <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-2.5 text-xs text-muted-foreground">
                {JSON.stringify(lead.requirements, null, 2)}
              </pre>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
