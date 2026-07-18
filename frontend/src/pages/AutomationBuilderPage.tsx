import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { AlertTriangle, ArrowLeft, Info, Save, Zap } from "lucide-react";
import {
  api,
  type AutomationDraft,
  type Vocabulary,
} from "@/lib/api";
import {
  describeTrigger,
  isActiveRun,
  type Condition,
  type Step,
  type Trigger,
} from "@/lib/recipe";
import { parseApiError } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { TriggerSentence } from "@/components/automations/TriggerSentence";
import { ConditionChips } from "@/components/automations/ConditionChips";
import { StepList } from "@/components/automations/StepList";
import { DraftBox } from "@/components/automations/DraftBox";

const EMPTY_TRIGGER: Trigger = { type: "event", event_type: "", source_system: null };

export function AutomationBuilderPage() {
  const { id } = useParams();
  const isEdit = Boolean(id);
  const navigate = useNavigate();

  const [vocab, setVocab] = useState<Vocabulary | null>(null);
  const [loading, setLoading] = useState(true);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [trigger, setTrigger] = useState<Trigger>(EMPTY_TRIGGER);
  const [conditions, setConditions] = useState<Condition[]>([]);
  const [steps, setSteps] = useState<Step[]>([]);
  const [dirty, setDirty] = useState(false);

  const [explanation, setExplanation] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [guard, setGuard] = useState<string | null>(null); // edit-guard 409 message
  const [saving, setSaving] = useState(false);

  // Load vocabulary (+ the automation itself in edit mode).
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const jobs: [Promise<Vocabulary>, Promise<unknown>] = [
      api.getVocabulary(),
      isEdit ? api.getAutomation(id as string) : Promise.resolve(null),
    ];
    Promise.all(jobs)
      .then(([v, a]) => {
        if (cancelled) return;
        setVocab(v);
        if (a && typeof a === "object") {
          const auto = a as {
            name: string;
            description: string | null;
            trigger: Trigger;
            conditions: Condition[];
            steps: Step[];
          };
          setName(auto.name);
          setDescription(auto.description ?? "");
          setTrigger(auto.trigger);
          setConditions(auto.conditions);
          setSteps(auto.steps);
        }
      })
      .catch((e) => !cancelled && toast.error(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [id, isEdit]);

  const mark = () => setDirty(true);

  const applyDraft = useCallback(
    (d: AutomationDraft) => {
      if (dirty && !window.confirm("Replace the current draft with the AI draft?")) return;
      setName(d.name);
      setDescription(d.description ?? "");
      setTrigger(d.trigger);
      setConditions(d.conditions);
      setSteps(d.steps);
      setExplanation(d.explanation);
      setSaveError(null);
      setDirty(false);
    },
    [dirty],
  );

  const save = async (activate: boolean) => {
    setSaving(true);
    setSaveError(null);
    setGuard(null);
    const body = { name: name.trim(), description: description.trim() || null, trigger, conditions, steps };
    try {
      let automationId: string;
      if (isEdit) {
        const patched = await api.patchAutomation(id as string, {
          ...body,
          status: activate ? "active" : undefined,
        });
        automationId = patched.id;
      } else {
        const created = await api.createAutomation(body);
        automationId = created.id;
        if (activate) await api.patchAutomation(automationId, { status: "active" });
      }
      toast.success(
        activate ? "Saved and activated" : "Saved (paused — activate when ready)",
      );
      navigate(`/automations/${automationId}`);
    } catch (e) {
      const { status, detail } = parseApiError(e);
      const message =
        typeof detail === "string"
          ? detail
          : typeof detail === "object" && detail && "message" in detail
            ? String((detail as { message: unknown }).message)
            : "Couldn't save this automation.";
      if (status === 409) setGuard(message);
      else setSaveError(message);
    } finally {
      setSaving(false);
    }
  };

  const cancelRunsAndRetry = async () => {
    if (!id) return;
    setSaving(true);
    try {
      const runs = await api.listRuns(id);
      await Promise.all(
        runs.filter((r) => isActiveRun(r.status)).map((r) => api.cancelRun(r.id)),
      );
      setGuard(null);
      await save(false);
    } catch (e) {
      toast.error(String(e));
      setSaving(false);
    }
  };

  if (loading || !vocab) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto p-6">
        <Skeleton className="mb-4 h-8 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const canSave = name.trim().length > 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-4 border-b px-6 py-4">
        <div className="min-w-0">
          <Link
            to={isEdit ? `/automations/${id}` : "/automations"}
            className="mb-1 inline-flex items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> {isEdit ? "Automation" : "Automations"}
          </Link>
          <h1 className="text-[17px] font-semibold tracking-tight">
            {isEdit ? "Edit automation" : "New automation"}
          </h1>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-5 p-6">
          {!isEdit && <DraftBox onDraft={applyDraft} />}

          {explanation && (
            <div className="flex gap-2 rounded-lg border border-info/30 bg-info/5 p-3 text-[13px]">
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-info" />
              <div>
                <p className="font-medium text-foreground">Review this draft</p>
                <p className="text-muted-foreground">{explanation}</p>
                <p className="mt-1 text-muted-foreground">
                  Nothing is created yet — edit anything, then save.
                </p>
              </div>
            </div>
          )}

          {saveError && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-[13px] text-destructive">
              {saveError}
            </div>
          )}

          {guard && (
            <div className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 p-3 text-[13px]">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
              <div className="flex-1">
                <p className="font-medium text-foreground">{guard}</p>
                <div className="mt-2 flex gap-2">
                  <Button size="sm" variant="outline" onClick={() => navigate(`/automations/${id}`)}>
                    View runs
                  </Button>
                  <Button size="sm" variant="destructive" onClick={cancelRunsAndRetry} disabled={saving}>
                    Cancel runs & save
                  </Button>
                </div>
              </div>
            </div>
          )}

          {/* Name + description */}
          <div className="space-y-3 rounded-xl border bg-card p-4 shadow-sm">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Name</label>
              <Input
                value={name}
                onChange={(e) => { setName(e.target.value); mark(); }}
                placeholder="e.g. Welcome a new lead"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Description (optional)
              </label>
              <Textarea
                value={description}
                onChange={(e) => { setDescription(e.target.value); mark(); }}
                placeholder="A one-line summary of what this does."
                rows={2}
              />
            </div>
          </div>

          {/* WHEN + IF */}
          <div className="space-y-4 rounded-xl border bg-card p-4 shadow-sm">
            <TriggerSentence
              trigger={trigger}
              vocabulary={vocab}
              onChange={(t) => { setTrigger(t); mark(); }}
            />
            <ConditionChips
              conditions={conditions}
              ctx={{ vocabulary: vocab, trigger, contextKeys: [] }}
              onChange={(c) => { setConditions(c); mark(); }}
            />
          </div>

          {/* THEN */}
          <div className="space-y-3 rounded-xl border bg-card p-4 shadow-sm">
            <h2 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <Zap className="h-3.5 w-3.5" /> Then, do this
            </h2>
            <StepList steps={steps} onChange={(s) => { setSteps(s); mark(); }} vocabulary={vocab} trigger={trigger} />
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between gap-4 border-t bg-card px-6 py-3">
        <p className="min-w-0 truncate text-[13px] text-muted-foreground">
          {describeTrigger(trigger)} · {steps.length} step{steps.length === 1 ? "" : "s"}
        </p>
        <div className="flex shrink-0 gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => save(false)}
            disabled={!canSave || saving}
          >
            <Save className="h-4 w-4" /> Save
          </Button>
          <Button size="sm" onClick={() => save(true)} disabled={!canSave || saving}>
            <Zap className="h-4 w-4" /> Save & activate
          </Button>
        </div>
      </div>
    </div>
  );
}
