import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Pause,
  Pencil,
  Play,
  Trash2,
  Zap,
} from "lucide-react";
import { GitBranch } from "lucide-react";
import { api, type Automation, type FieldCatalog, type Run } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { bindingEditRoute, describeBinding } from "@/lib/pipeline";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import { TriggerSentence } from "@/components/automations/TriggerSentence";
import { ConditionChips } from "@/components/automations/ConditionChips";
import { StepCard } from "@/components/automations/StepCard";
import { RunList } from "@/components/automations/RunList";
import { RunTimeline } from "@/components/automations/RunTimeline";
import { ConfirmDialog } from "@/components/automations/ConfirmDialog";

export function AutomationDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [automation, setAutomation] = useState<Automation | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Run | null>(null);
  const [showJson, setShowJson] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // The field catalog powers read-mode labels ("…to Phone", not "{{...}}").
  const [catalog, setCatalog] = useState<FieldCatalog | undefined>(undefined);

  useEffect(() => {
    api.getVocabulary().then((v) => setCatalog(v.field_catalog)).catch(() => {});
  }, []);

  const load = useCallback(async () => {
    const [a, r] = await Promise.all([api.getAutomation(id), api.listRuns(id)]);
    setAutomation(a);
    setRuns(r);
    // keep the open drawer in sync with the freshest run row
    setSelected((cur) => (cur ? r.find((x) => x.id === cur.id) ?? cur : cur));
  }, [id]);

  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    load()
      .catch((e) => !cancelled && toast.error(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [load]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refetch = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => loadRef.current().catch(() => {}), 300);
    };
    const channel = supabase
      .channel(`automation-${id}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "automations" }, refetch)
      .on("postgres_changes", { event: "*", schema: "public", table: "automation_runs" }, refetch)
      .subscribe();
    return () => {
      if (timer) clearTimeout(timer);
      supabase.removeChannel(channel);
    };
  }, [id]);

  const onToggle = async () => {
    if (!automation) return;
    const next = automation.status === "active" ? "paused" : "active";
    try {
      await api.patchAutomation(automation.id, { status: next });
      await load();
    } catch (e) {
      toast.error(String(e));
    }
  };

  const onDelete = async () => {
    if (!automation) return;
    try {
      await api.deleteAutomation(automation.id);
      toast.success(`Deleted “${automation.name}”`);
      navigate("/automations");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const onCancelRun = async (run: Run) => {
    try {
      await api.cancelRun(run.id);
      toast.success("Run cancelled");
      setSelected(null);
      await load();
    } catch (e) {
      toast.error(String(e));
    }
  };

  if (loading) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto p-6">
        <Skeleton className="mb-4 h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (!automation) {
    return (
      <div className="p-6">
        <EmptyState
          icon={Zap}
          title="Automation not found"
          description="It may have been deleted."
          action={
            <Button size="sm" variant="outline" onClick={() => navigate("/automations")}>
              Back to Automations
            </Button>
          }
        />
      </div>
    );
  }

  const active = automation.status === "active";
  const bindingLabel = describeBinding(automation.binding);
  const editRoute =
    bindingEditRoute(automation.binding) ?? `/automations/${automation.id}/edit`;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-start justify-between gap-4 border-b px-6 py-4">
        <div className="min-w-0">
          <Link
            to="/automations"
            className="mb-1 inline-flex items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> Automations
          </Link>
          <div className="flex items-center gap-2">
            <h1 className="truncate text-[17px] font-semibold tracking-tight">
              {automation.name}
            </h1>
            <Badge variant={active ? "success" : "secondary"}>
              {active ? "Active" : "Paused"}
            </Badge>
            {automation.requires_approval && (
              <Badge variant="warning">Requires approval</Badge>
            )}
            {bindingLabel && (
              <Badge variant="info" className="gap-1">
                <GitBranch className="h-3 w-3" /> {bindingLabel}
              </Badge>
            )}
          </div>
          {automation.description && (
            <p className="mt-0.5 text-[13px] text-muted-foreground">
              {automation.description}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button size="sm" variant="outline" onClick={onToggle}>
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
          <Button size="sm" variant="outline" onClick={() => navigate(editRoute)}>
            <Pencil className="h-3.5 w-3.5" /> Edit
          </Button>
          <Button size="sm" variant="outline" onClick={() => setConfirmDelete(true)}>
            <Trash2 className="h-3.5 w-3.5" /> Delete
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-6 p-6">
          {/* Recipe summary */}
          <section className="space-y-3 rounded-xl border bg-card p-4 shadow-sm">
            <h2 className="text-[13px] font-semibold text-muted-foreground">Recipe</h2>
            <TriggerSentence trigger={automation.trigger} />
            <ConditionChips conditions={automation.conditions} catalog={catalog} />
            <div className="space-y-2">
              {automation.steps.map((step, i) => (
                <StepCard key={i} step={step} index={i} catalog={catalog} />
              ))}
            </div>
            <button
              onClick={() => setShowJson((v) => !v)}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              {showJson ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
              Technical detail (recipe JSON)
            </button>
            {showJson && (
              <pre className="overflow-x-auto rounded-md bg-muted p-2 text-xs text-muted-foreground">
                {JSON.stringify(
                  {
                    trigger: automation.trigger,
                    conditions: automation.conditions,
                    steps: automation.steps,
                  },
                  null,
                  2,
                )}
              </pre>
            )}
          </section>

          {/* Runs */}
          <section className="space-y-3">
            <h2 className="text-[13px] font-semibold text-muted-foreground">
              Run history
            </h2>
            {runs.length === 0 ? (
              <div className="rounded-lg border bg-card p-6 text-center text-[13px] text-muted-foreground">
                No runs yet. Trigger this automation or wait for its event.
              </div>
            ) : (
              <RunList runs={runs} onSelect={setSelected} />
            )}
          </section>
        </div>
      </div>

      {selected && (
        <RunTimeline
          run={selected}
          onClose={() => setSelected(null)}
          onCancel={onCancelRun}
        />
      )}

      <ConfirmDialog
        open={confirmDelete}
        title="Delete automation?"
        body={`“${automation.name}” and its run history will be removed. This can't be undone.`}
        confirmLabel="Delete"
        destructive
        onConfirm={onDelete}
        onClose={() => setConfirmDelete(false)}
      />
    </div>
  );
}
