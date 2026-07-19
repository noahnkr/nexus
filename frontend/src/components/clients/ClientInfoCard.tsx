import { useState } from "react";
import { Pencil, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import type { ClientDetail, ClientPatch } from "@/lib/api";

// Free-text tag editor (languages / preferences), matching the caregiver drawer's
// TagInput. Enter or Add appends; the × removes.
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
          <Plus className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-sm">{value?.trim() ? value : "—"}</p>
    </div>
  );
}

const sameSet = (a: string[], b: string[]) =>
  a.length === b.length && a.every((x, i) => x === b[i]);

// The client's contact record: name / phone / email / address / zip (inline edit)
// plus languages & preferences tag editors. Saves only the changed fields via
// onPatch, which emits one client.updated naming them.
export function ClientInfoCard({
  client,
  onPatch,
  busy,
}: {
  client: ClientDetail;
  onPatch: (patch: ClientPatch) => Promise<void>;
  busy: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(client.name);
  const [phone, setPhone] = useState(client.phone ?? "");
  const [email, setEmail] = useState(client.email ?? "");
  const [address, setAddress] = useState(client.address ?? "");
  const [zip, setZip] = useState(client.zip ?? "");
  const [languages, setLanguages] = useState<string[]>(client.languages);
  const [preferences, setPreferences] = useState<string[]>(client.preferences);

  const startEdit = () => {
    setName(client.name);
    setPhone(client.phone ?? "");
    setEmail(client.email ?? "");
    setAddress(client.address ?? "");
    setZip(client.zip ?? "");
    setLanguages(client.languages);
    setPreferences(client.preferences);
    setEditing(true);
  };

  const save = async () => {
    const patch: ClientPatch = {};
    if (name.trim() && name.trim() !== client.name) patch.name = name.trim();
    if ((phone.trim() || null) !== client.phone) patch.phone = phone.trim() || null;
    if ((email.trim() || null) !== client.email) patch.email = email.trim() || null;
    if ((address.trim() || null) !== client.address) patch.address = address.trim() || null;
    if ((zip.trim() || null) !== client.zip) patch.zip = zip.trim() || null;
    if (!sameSet(languages, client.languages)) patch.languages = languages;
    if (!sameSet(preferences, client.preferences)) patch.preferences = preferences;
    if (Object.keys(patch).length > 0) await onPatch(patch);
    setEditing(false);
  };

  return (
    <Card>
      <CardContent className="space-y-4 p-4">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Contact
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
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Address</label>
                <Input value={address} onChange={(e) => setAddress(e.target.value)} />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">ZIP</label>
                <Input value={zip} onChange={(e) => setZip(e.target.value)} className="w-28" />
              </div>
            </div>
            <TagInput
              label="Languages"
              tags={languages}
              onChange={setLanguages}
              placeholder="en, es, tl…"
            />
            <TagInput
              label="Preferences"
              tags={preferences}
              onChange={setPreferences}
              placeholder="female caregiver, no pets…"
            />
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
              <Field label="Phone" value={client.phone} />
              <Field label="Email" value={client.email} />
              <Field label="Address" value={client.address} />
              <Field label="ZIP" value={client.zip} />
              <Field label="Languages" value={client.languages.join(", ") || null} />
              <Field label="Preferences" value={client.preferences.join(", ") || null} />
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
