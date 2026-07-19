import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Info } from "lucide-react";
import { api, type AgentTone, type TenantSettings } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, type SelectOption } from "@/components/ui/Select";

const MAX_INSTRUCTIONS = 4000; // mirrors services/settings.py

const TONE_OPTIONS: SelectOption<AgentTone>[] = [
  { value: "balanced", label: "Balanced" },
  { value: "professional", label: "Professional" },
  { value: "friendly", label: "Friendly" },
  { value: "concise", label: "Concise" },
];

// Instructions shape HOW the assistant answers; documents are WHAT it knows. They
// live on the same page because users conflate the two, and the explainer below is
// there to draw the line. Saved per-tenant and appended to the chat system prompt
// after the core persona — they can't loosen the approval gate.
export function InstructionsTab() {
  const [saved, setSaved] = useState<TenantSettings | null>(null);
  const [instructions, setInstructions] = useState("");
  const [tone, setTone] = useState<AgentTone>("balanced");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getSettings()
      .then((s) => {
        if (cancelled) return;
        setSaved(s);
        setInstructions(s.agent_instructions);
        setTone(s.agent_tone);
      })
      .catch((e) => !cancelled && toast.error(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  const dirty =
    saved !== null &&
    (instructions !== saved.agent_instructions || tone !== saved.agent_tone);
  const tooLong = instructions.length > MAX_INSTRUCTIONS;

  const save = async () => {
    setSaving(true);
    try {
      const next = await api.updateSettings({
        agent_instructions: instructions,
        agent_tone: tone,
      });
      setSaved(next);
      setInstructions(next.agent_instructions);
      setTone(next.agent_tone);
      toast.success("Instructions saved");
    } catch (e) {
      toast.error(String(e));
    } finally {
      setSaving(false);
    }
  };

  if (saved === null) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-9 w-48" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  return (
    <div className="max-w-2xl space-y-5">
      <div className="flex items-start gap-2.5 rounded-lg border bg-muted/30 p-3">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <p className="text-xs leading-relaxed text-muted-foreground">
          Applied to every chat response. Documents remain the assistant's
          knowledge; instructions shape its behavior. These never override its
          safety rules — actions that reach outside the system still need your
          approval.
        </p>
      </div>

      <div className="max-w-xs">
        <label className="mb-1 block text-xs font-medium text-muted-foreground">
          Tone
        </label>
        <Select<AgentTone>
          value={tone}
          onChange={setTone}
          options={TONE_OPTIONS}
          aria-label="Assistant tone"
        />
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-muted-foreground">
          Instructions
        </label>
        <Textarea
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          rows={12}
          placeholder={
            "e.g. Always refer to caregivers by first name.\n" +
            "Sign off longer answers as “The Sunrise team”.\n" +
            "When summarizing a client, lead with their care level."
          }
          className="min-h-[200px]"
        />
        <div className="mt-1 flex items-center justify-between text-xs">
          <span className={tooLong ? "text-destructive" : "text-muted-foreground"}>
            {instructions.length.toLocaleString()} /{" "}
            {MAX_INSTRUCTIONS.toLocaleString()} characters
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button onClick={save} disabled={saving || !dirty || tooLong}>
          Save instructions
        </Button>
        {dirty && !saving && (
          <span className="text-xs text-muted-foreground">Unsaved changes</span>
        )}
      </div>
    </div>
  );
}
