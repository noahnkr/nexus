import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ListTodo, Plus } from "lucide-react";
import { api, type Task, type TaskCreate, type TaskStatus } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { TaskFilters } from "@/components/tasks/TaskFilters";
import { TaskCard } from "@/components/tasks/TaskCard";
import { CreateTaskDialog } from "@/components/tasks/CreateTaskDialog";

const DEFAULT_STATUS = "pending,in_progress"; // the "Open" tab
const PAGE_SIZE = 50;

// URL <-> active status tab. Absent param means the default Open view; the "all"
// sentinel means no status filter (so absence can still represent the default).
function readStatus(sp: URLSearchParams): string {
  const raw = sp.get("status");
  if (raw === null) return DEFAULT_STATUS;
  if (raw === "all") return "";
  return raw;
}

export function TasksPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const status = readStatus(searchParams);
  const priority = searchParams.get("priority") ?? "";

  const [tasks, setTasks] = useState<Task[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [creating, setCreating] = useState(false);

  const apiStatus = status === "" ? undefined : status;
  const apiPriority = priority || undefined;

  // Home's "New task" quick action deep-links here with ?create=1 — open the
  // dialog once, then strip the param so a refresh doesn't reopen it.
  useEffect(() => {
    if (searchParams.get("create") === null) return;
    setCreating(true);
    const next = new URLSearchParams(searchParams);
    next.delete("create");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const loadFirst = useCallback(async () => {
    const page = await api.listTasks({
      status: apiStatus,
      priority: apiPriority,
      limit: PAGE_SIZE,
    });
    setTasks(page.tasks);
    setNextCursor(page.next_cursor);
  }, [apiStatus, apiPriority]);

  // Latest loader for the Realtime handler to call without re-subscribing.
  const loadRef = useRef(loadFirst);
  loadRef.current = loadFirst;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadFirst()
      .catch((e) => !cancelled && toast.error(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [loadFirst]);

  // Realtime: any task/pending_action change refetches the first page. Tasks are
  // mutable, so refetch-on-signal is simpler and always consistent at this scale.
  useEffect(() => {
    // supabase-js forwards the signed-in session token to Realtime automatically.
    const refetch = () => loadRef.current().catch(() => {});
    const channel = supabase
      .channel("tasks-changes")
      .on("postgres_changes", { event: "*", schema: "public", table: "tasks" }, refetch)
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "pending_actions" },
        refetch,
      )
      .subscribe();
    return () => {
      supabase.removeChannel(channel);
    };
  }, []);

  const patchStatusParam = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams(searchParams);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setSearchParams(next, { replace: true });
  };

  const onStatusChange = (s: string) => {
    // Open is the default (no param); All uses the "all" sentinel.
    if (s === DEFAULT_STATUS) patchStatusParam({ status: undefined });
    else if (s === "") patchStatusParam({ status: "all" });
    else patchStatusParam({ status: s });
  };

  const loadMore = async () => {
    if (!nextCursor) return;
    setLoadingMore(true);
    try {
      const page = await api.listTasks({
        status: apiStatus,
        priority: apiPriority,
        limit: PAGE_SIZE,
        cursor: nextCursor,
      });
      setTasks((prev) => {
        const seen = new Set(prev.map((t) => t.id));
        return [...prev, ...page.tasks.filter((t) => !seen.has(t.id))];
      });
      setNextCursor(page.next_cursor);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setLoadingMore(false);
    }
  };

  const onTransition = async (id: string, next: TaskStatus) => {
    try {
      await api.patchTask(id, next);
      await loadFirst();
    } catch (e) {
      toast.error(String(e));
    }
  };

  const onApprove = async (id: string) => {
    try {
      const res = await api.approveAction(id);
      toast[res.action.status === "failed" ? "error" : "success"](
        res.action.result?.summary ?? res.action.result?.error ?? "Resolved",
      );
      await loadFirst();
    } catch (e) {
      toast.error(String(e));
    }
  };

  const onReject = async (id: string, note?: string) => {
    try {
      await api.rejectAction(id, note);
      await loadFirst();
    } catch (e) {
      toast.error(String(e));
    }
  };

  const onCreate = async (body: TaskCreate) => {
    try {
      await api.createTask(body);
      await loadFirst();
      toast.success("Task created");
    } catch (e) {
      toast.error(String(e));
      throw e;
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Tasks"
        description="Work that needs doing, plus actions waiting on your approval."
        action={
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" /> New task
          </Button>
        }
      />

      <div className="flex min-h-0 flex-1 flex-col gap-4 p-6">
        <TaskFilters
          status={status}
          priority={priority}
          onStatusChange={onStatusChange}
          onPriorityChange={(p) => patchStatusParam({ priority: p || undefined })}
        />

        <div className="min-h-0 flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex flex-col gap-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-24 w-full" />
              ))}
            </div>
          ) : tasks.length === 0 ? (
            <EmptyState
              icon={ListTodo}
              title="No tasks here"
              description="Nothing matches these filters. Create a task, or approvals drafted in chat will land here."
              action={
                <Button size="sm" variant="outline" onClick={() => setCreating(true)}>
                  <Plus className="h-4 w-4" /> New task
                </Button>
              }
            />
          ) : (
            <div className="flex flex-col gap-3">
              {tasks.map((t) => (
                <TaskCard
                  key={t.id}
                  task={t}
                  onTransition={onTransition}
                  onApprove={onApprove}
                  onReject={onReject}
                />
              ))}
              {nextCursor && (
                <div className="flex justify-center py-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={loadMore}
                    disabled={loadingMore}
                  >
                    {loadingMore ? "Loading…" : "Load more"}
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <CreateTaskDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreate={onCreate}
      />
    </div>
  );
}
