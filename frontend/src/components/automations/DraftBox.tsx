import { useState } from "react";
import { Sparkles, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api, type AutomationDraft } from "@/lib/api";
import { parseApiError } from "@/lib/utils";

// The "describe what you want to automate" box on the create page. Calls the draft
// endpoint and hands the (unsaved) draft back to the builder to prefill — nothing
// is created until the user saves. The typed description is kept on failure so the
// user can retry after tweaking it.
export function DraftBox({ onDraft }: { onDraft: (draft: AutomationDraft) => void }) {
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const draft = async () => {
    if (!description.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const d = await api.draftAutomation(description.trim());
      onDraft(d);
    } catch (e) {
      const { detail } = parseApiError(e);
      const msg =
        typeof detail === "object" && detail && "message" in detail
          ? String((detail as { message: unknown }).message)
          : typeof detail === "string"
            ? detail
            : "Couldn't draft that — try describing it differently.";
      setError(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border bg-gradient-to-br from-primary/5 to-transparent p-4 shadow-sm">
      <div className="mb-2 flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-primary" />
        <h2 className="text-sm font-semibold">Describe it and let AI draft it</h2>
      </div>
      <p className="mb-3 text-[13px] text-muted-foreground">
        Write what you want to happen in plain language. You'll review and can edit
        every part before anything is saved.
      </p>
      <Textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="When a new lead comes in from WelcomeHome, wait a day, then text them a personalized welcome."
        rows={3}
      />
      {error && <p className="mt-2 text-[13px] text-destructive">{error}</p>}
      <div className="mt-3 flex justify-end">
        <Button size="sm" onClick={draft} disabled={busy || !description.trim()}>
          <Wand2 className="h-4 w-4" /> {busy ? "Drafting…" : "Draft automation"}
        </Button>
      </div>
    </div>
  );
}
