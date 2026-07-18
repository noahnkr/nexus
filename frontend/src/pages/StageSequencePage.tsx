import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  AlertTriangle,
  ArrowLeft,
  Pause,
  Play,
  Save,
  Trash2,
  Zap,
} from "lucide-react";
import { api, type Automation, type Vocabulary } from "@/lib/api";
import { isActiveRun, type Condition, type Step } from "@/lib/recipe";
import { parseApiError } from "@/lib/utils";
import { getPipelineView } from "@/lib/pipeline";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { ConditionChips } from "@/components/automations/ConditionChips";
import { StepList } from "@/components/automations/StepList";
import { ConfirmDialog } from "@/components/automations/ConfirmDialog";

// The per-stage outreach sequence builder — deliberately narrower than the M8
// free-form builder: the trigger is FIXED by the stage (rendered as a sentence,
// not editable), and the tool palette is the view's allowlist. A sequence is an
// ordinary M7 automation tagged with binding {view, stage}; the standard
// create/patch path saves it. View-config-driven so M10 reuses this page.
export function StageSequencePage({ view }: { view: string }) {
  const { stage = "" } = useParams();
  const navigate = useNavigate();
  const config = getPipelineView(view);

  const [vocab, setVocab] = useState<Vocabulary | null>(null);
  const [existing, setExisting] = useState<Automation | null>(null);
  const [loading, setLoading] = useState(true);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [conditions, setConditions] = useState<Condition[]>([]);
  const [steps, setSteps] = useState<Step[]>([]);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [guard, setGuard] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Memoized so the trigger convention is a stable reference — otherwise it would
  // recompute every render and cascade through isManaged → load → the load effect,
  // refetching in a loop.
  const convention = useMemo(() => config?.buildTrigger(stage), [config, stage]);
  const stageLabel =
    config?.stages.find((s) => s.key === stage)?.label ?? stage;
  const validStage = Boolean(config && config.sequenceStages.includes(stage));

  // Strip the managed condition (payload.to = stage) so only extra IF conditions
  // are editable — the managed one is shown as prose in the header.
  const isManaged = useCallback(
    (c: Condition) =>
      convention?.managedCondition != null &&
      c.field === convention.managedCondition.field &&
      c.op === convention.managedCondition.op &&
      c.value === convention.managedCondition.value,
    [convention],
  );

  const load = useCallback(async () => {
    const [v, list] = await Promise.all([
      api.getVocabulary(),
      api.listAutomations({ view }),
    ]);
    setVocab(v);
    const found = list.find((a) => a.binding?.stage === stage) ?? null;
    setExisting(found);
    if (found) {
      setName(found.name);
      setDescription(found.description ?? "");
      setConditions((found.conditions as Condition[]).filter((c) => !isManaged(c)));
      setSteps(found.steps as Step[]);
    } else {
      setName(config?.defaultName(stage) ?? "");
      setDescription("");
      setConditions([]);
      setSteps([]);
    }
  }, [view, stage, config, isManaged]);

  useEffect(() => {
    if (!validStage) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    load()
      .catch((e) => !cancelled && toast.error(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [load, validStage]);

  // Filter the vocabulary's tools to the view's allowlist — the constrained
  // palette. Everything else (functions, operators, generate) stays available.
  const scopedVocab: Vocabulary | null = vocab
    ? { ...vocab, tools: vocab.tools.filter((t) => config!.toolAllowlist.includes(t.name)) }
    : null;

  const buildBody = () => {
    const managed = convention?.managedCondition ? [convention.managedCondition] : [];
    return {
      name: name.trim(),
      description: description.trim() || null,
      trigger: convention!.trigger,
      conditions: [...managed, ...conditions],
      steps,
      binding: { view, stage },
    };
  };

  const save = async (activate: boolean) => {
    setSaving(true);
    setSaveError(null);
    setGuard(null);
    try {
      if (existing) {
        await api.patchAutomation(existing.id, {
          ...buildBody(),
          status: activate ? "active" : undefined,
        });
        toast.success(activate ? "Saved and activated" : "Saved");
      } else {
        const created = await api.createAutomation(buildBody());
        if (activate) await api.patchAutomation(created.id, { status: "active" });
        toast.success(activate ? "Sequence created and activated" : "Sequence created (paused)");
      }
      await load();
    } catch (e) {
      const { status, detail } = parseApiError(e);
      const message =
        typeof detail === "string"
          ? detail
          : typeof detail === "object" && detail && "message" in detail
            ? String((detail as { message: unknown }).message)
            : "Couldn't save this sequence.";
      // On CREATE a 409 is the one-sequence-per-stage race; reload to edit the
      // existing one. On PATCH a 409 is the edit-guard (runs in flight).
      if (status === 409 && !existing) {
        toast.error(message);
        await load();
      } else if (status === 409) {
        setGuard(message);
      } else {
        setSaveError(message);
      }
    } finally {
      setSaving(false);
    }
  };

  const cancelRunsAndRetry = async () => {
    if (!existing) return;
    setSaving(true);
    try {
      const runs = await api.listRuns(existing.id);
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

  const toggleActive = async () => {
    if (!existing) return;
    setSaving(true);
    try {
      await api.patchAutomation(existing.id, {
        status: existing.status === "active" ? "paused" : "active",
      });
      await load();
    } catch (e) {
      toast.error(String(e));
    } finally {
      setSaving(false);
    }
  };

  const doDelete = async () => {
    if (!existing) return;
    try {
      await api.deleteAutomation(existing.id);
      toast.success("Sequence deleted");
      navigate(config!.directoryRoute);
    } catch (e) {
      toast.error(String(e));
    }
  };

  if (!config || !validStage) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="border-b px-6 py-4">
          <h1 className="text-[17px] font-semibold tracking-tight">Stage sequence</h1>
        </div>
        <div className="p-6 text-sm text-muted-foreground">
          This stage doesn&apos;t support a sequence.{" "}
          <Link to={config?.directoryRoute ?? "/"} className="text-primary hover:underline">
            Go back
          </Link>
        </div>
      </div>
    );
  }

  if (loading || !scopedVocab) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto p-6">
        <Skeleton className="mb-4 h-8 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const active = existing?.status === "active";
  const canSave = name.trim().length > 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-4 border-b px-6 py-4">
        <div className="min-w-0">
          <Link
            to={config.directoryRoute}
            className="mb-1 inline-flex items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> {config.label}
          </Link>
          <h1 className="text-[17px] font-semibold tracking-tight">
            {stageLabel} sequence
          </h1>
        </div>
        {existing && (
          <Badge variant={active ? "success" : "secondary"}>
            {active ? "Active" : "Paused"}
          </Badge>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-5 p-6">
          {/* Fixed trigger sentence — not editable (the stage owns it). */}
          <div className="rounded-xl border border-info/30 bg-info/5 p-4">
            <p className="text-[13px] font-medium text-foreground">
              When a {config.entityType} enters{" "}
              <span className="font-semibold">{stageLabel}</span>
            </p>
            <p className="mt-0.5 text-[12px] text-muted-foreground">
              This sequence runs automatically each time a {config.entityType} reaches
              this stage. The trigger is fixed — add extra conditions and steps below.
            </p>
          </div>

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
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={cancelRunsAndRetry}
                    disabled={saving}
                  >
                    Cancel runs &amp; save
                  </Button>
                </div>
              </div>
            </div>
          )}

          {/* Name + description */}
          <div className="space-y-3 rounded-xl border bg-card p-4 shadow-sm">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Name</label>
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Description (optional)
              </label>
              <Textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
              />
            </div>
          </div>

          {/* Extra IF conditions (the managed to=stage condition is implied above) */}
          <div className="rounded-xl border bg-card p-4 shadow-sm">
            <ConditionChips
              conditions={conditions}
              ctx={{ vocabulary: scopedVocab, trigger: convention!.trigger, contextKeys: [] }}
              onChange={setConditions}
              label="And only if"
              addLabel="Add condition"
            />
          </div>

          {/* THEN */}
          <div className="space-y-3 rounded-xl border bg-card p-4 shadow-sm">
            <h2 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <Zap className="h-3.5 w-3.5" /> Then, do this
            </h2>
            <StepList steps={steps} onChange={setSteps} vocabulary={scopedVocab} trigger={convention!.trigger} />
          </div>

          {existing && (
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={toggleActive} disabled={saving}>
                {active ? (
                  <>
                    <Pause className="h-3.5 w-3.5" /> Pause
                  </>
                ) : (
                  <>
                    <Play className="h-3.5 w-3.5" /> Activate
                  </>
                )}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmDelete(true)}
                className="text-destructive hover:text-destructive"
              >
                <Trash2 className="h-3.5 w-3.5" /> Delete
              </Button>
              <Link
                to={`/automations/${existing.id}`}
                className="ml-auto text-[12px] text-muted-foreground hover:text-foreground"
              >
                View in Automations Center →
              </Link>
            </div>
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between gap-4 border-t bg-card px-6 py-3">
        <p className="min-w-0 truncate text-[13px] text-muted-foreground">
          {steps.length} step{steps.length === 1 ? "" : "s"}
          {existing ? "" : " · new sequences start paused"}
        </p>
        <div className="flex shrink-0 gap-2">
          <Button variant="outline" size="sm" onClick={() => save(false)} disabled={!canSave || saving}>
            <Save className="h-4 w-4" /> Save
          </Button>
          {!active && (
            <Button size="sm" onClick={() => save(true)} disabled={!canSave || saving}>
              <Zap className="h-4 w-4" /> Save &amp; activate
            </Button>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete this sequence?"
        body="The stage will no longer trigger any outreach. This can't be undone."
        confirmLabel="Delete"
        destructive
        onConfirm={() => {
          setConfirmDelete(false);
          void doDelete();
        }}
        onClose={() => setConfirmDelete(false)}
      />
    </div>
  );
}
