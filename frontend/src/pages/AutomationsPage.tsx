import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Plus, Zap } from "lucide-react";
import { api, type Automation } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { AutomationCard } from "@/components/automations/AutomationCard";
import { ConfirmDialog } from "@/components/automations/ConfirmDialog";

// The Automations Center grid: every recipe at a glance, with pause/resume, delete,
// and deep-links into detail/builder. Live via Realtime on automations + runs (the
// TasksPage refetch pattern, lightly debounced so a run burst doesn't thrash).
export function AutomationsPage() {
  const navigate = useNavigate();
  const [automations, setAutomations] = useState<Automation[]>([]);
  const [loading, setLoading] = useState(true);
  const [toDelete, setToDelete] = useState<Automation | null>(null);

  const load = useCallback(async () => {
    const rows = await api.listAutomations();
    setAutomations(rows);
  }, []);

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

  // Realtime: debounce refetch so a run advancing through several steps (many row
  // updates) collapses into one reload.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refetch = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => loadRef.current().catch(() => {}), 300);
    };
    const channel = supabase
      .channel("automations-changes")
      .on("postgres_changes", { event: "*", schema: "public", table: "automations" }, refetch)
      .on("postgres_changes", { event: "*", schema: "public", table: "automation_runs" }, refetch)
      .subscribe();
    return () => {
      if (timer) clearTimeout(timer);
      supabase.removeChannel(channel);
    };
  }, []);

  const onToggle = async (a: Automation) => {
    const next = a.status === "active" ? "paused" : "active";
    // optimistic
    setAutomations((prev) =>
      prev.map((x) => (x.id === a.id ? { ...x, status: next } : x)),
    );
    try {
      await api.patchAutomation(a.id, { status: next });
      await load();
    } catch (e) {
      toast.error(String(e));
      await load();
    }
  };

  const onConfirmDelete = async () => {
    if (!toDelete) return;
    try {
      await api.deleteAutomation(toDelete.id);
      toast.success(`Deleted “${toDelete.name}”`);
      setToDelete(null);
      await load();
    } catch (e) {
      toast.error(String(e));
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Automations"
        description="Recipes that watch for events and run steps for you — no code."
        action={
          <Button size="sm" onClick={() => navigate("/automations/new")}>
            <Plus className="h-4 w-4" /> New automation
          </Button>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto p-6">
        {loading ? (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-40 w-full" />
            ))}
          </div>
        ) : automations.length === 0 ? (
          <EmptyState
            icon={Zap}
            title="No automations yet"
            description="Build a recipe that reacts to an event or runs on a schedule — describe it in plain language and let the assistant draft it."
            action={
              <Button size="sm" onClick={() => navigate("/automations/new")}>
                <Plus className="h-4 w-4" /> New automation
              </Button>
            }
          />
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {automations.map((a) => (
              <AutomationCard
                key={a.id}
                automation={a}
                onToggle={onToggle}
                onDelete={setToDelete}
              />
            ))}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={toDelete !== null}
        title="Delete automation?"
        body={
          toDelete
            ? `“${toDelete.name}” and its run history will be removed. This can't be undone.`
            : ""
        }
        confirmLabel="Delete"
        destructive
        onConfirm={onConfirmDelete}
        onClose={() => setToDelete(null)}
      />
    </div>
  );
}
