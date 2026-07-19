import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  MoreVertical,
  Pause,
  Play,
  Pencil,
  ShieldAlert,
  Trash2,
  Eye,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn, relativeTime } from "@/lib/utils";
import { describeTrigger, RUN_STATUS_META } from "@/lib/recipe";
import { bindingEditRoute, describeBinding } from "@/lib/pipeline";
import { GitBranch } from "lucide-react";
import type { Automation } from "@/lib/api";

// One automation in the grid: name, status pill, plain-language trigger line, an
// approval chip when any step is gated, active-run and last-run lines, a
// pause/resume toggle, and an overflow menu (View / Edit / Delete). Never executes
// anything — every action routes through the M7 API via the page's handlers.
export function AutomationCard({
  automation,
  onToggle,
  onDelete,
  onRun,
}: {
  automation: Automation;
  onToggle: (a: Automation) => void;
  onDelete: (a: Automation) => void;
  onRun: (a: Automation) => Promise<void>;
}) {
  const navigate = useNavigate();
  const active = automation.status === "active";
  // A manual automation has no trigger to be active FOR — `status` is ignored by
  // the run endpoint entirely — so pausing it is a control that does nothing.
  // It gets a neutral "Manual" badge and a Run button instead (M15c).
  const manual = automation.trigger?.type === "manual";
  const [running, setRunning] = useState(false);

  const doRun = async () => {
    setRunning(true);
    try {
      await onRun(automation);
    } finally {
      setRunning(false);
    }
  };
  const last = automation.last_run;
  // A bound sequence (9b): show its "Leads · Contacted" chip and route Edit to the
  // view's stage builder (one editing surface per recipe — the generic builder
  // would let the trigger drift from the binding).
  const bindingLabel = describeBinding(automation.binding);
  const editRoute =
    bindingEditRoute(automation.binding) ?? `/automations/${automation.id}/edit`;

  return (
    <div className="group flex flex-col gap-3 rounded-xl border bg-card p-4 shadow-sm transition-all hover:border-primary/40 hover:shadow-md">
      <div className="flex items-start justify-between gap-2">
        <Link
          to={`/automations/${automation.id}`}
          className="min-w-0 flex-1 text-[15px] font-semibold tracking-tight hover:text-primary"
        >
          <span className="line-clamp-2">{automation.name}</span>
        </Link>
        <div className="flex shrink-0 items-center gap-1.5">
          {manual ? (
            <Badge variant="outline">Manual</Badge>
          ) : (
            <Badge variant={active ? "success" : "secondary"}>
              {active ? "Active" : "Paused"}
            </Badge>
          )}
          <OverflowMenu
            onView={() => navigate(`/automations/${automation.id}`)}
            onEdit={() => navigate(editRoute)}
            onDelete={() => onDelete(automation)}
          />
        </div>
      </div>

      <p className="text-[13px] text-muted-foreground">
        {describeTrigger(automation.trigger)}
        <span className="text-muted-foreground/60">
          {" · "}
          {automation.steps.length} step{automation.steps.length === 1 ? "" : "s"}
        </span>
      </p>

      <div className="flex flex-wrap items-center gap-1.5">
        {bindingLabel && (
          <Badge variant="info" className="gap-1">
            <GitBranch className="h-3 w-3" /> {bindingLabel}
          </Badge>
        )}
        {automation.requires_approval && (
          <Badge variant="warning" className="gap-1">
            <ShieldAlert className="h-3 w-3" /> Requires approval
          </Badge>
        )}
        {automation.active_runs > 0 && (
          <Badge variant="info">
            {automation.active_runs} run{automation.active_runs === 1 ? "" : "s"} in flight
          </Badge>
        )}
      </div>

      <div className="mt-auto flex items-center justify-between gap-2 border-t pt-3">
        <span className="min-w-0 truncate text-[12px] text-muted-foreground">
          {last ? (
            <>
              Last run{" "}
              <span
                className={cn(
                  "font-medium",
                  last.status === "failed" && "text-destructive",
                )}
              >
                {RUN_STATUS_META[last.status]?.label.toLowerCase() ?? last.status}
              </span>{" "}
              {relativeTime(last.at)}
            </>
          ) : (
            "No runs yet"
          )}
        </span>
        {manual ? (
          <Button size="sm" onClick={doRun} disabled={running} className="shrink-0">
            <Play className="h-3.5 w-3.5" /> {running ? "Starting…" : "Run"}
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            onClick={() => onToggle(automation)}
            className="shrink-0"
          >
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
        )}
      </div>
    </div>
  );
}

function OverflowMenu({
  onView,
  onEdit,
  onDelete,
}: {
  onView: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const item =
    "flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] transition-colors hover:bg-muted";

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        aria-label="Automation actions"
      >
        <MoreVertical className="h-4 w-4" />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 w-40 overflow-hidden rounded-lg border bg-card shadow-lg">
          <button className={item} onClick={() => { setOpen(false); onView(); }}>
            <Eye className="h-4 w-4 text-muted-foreground" /> View
          </button>
          <button className={item} onClick={() => { setOpen(false); onEdit(); }}>
            <Pencil className="h-4 w-4 text-muted-foreground" /> Edit
          </button>
          <div className="border-t" />
          <button
            className={cn(item, "text-destructive")}
            onClick={() => { setOpen(false); onDelete(); }}
          >
            <Trash2 className="h-4 w-4" /> Delete
          </button>
        </div>
      )}
    </div>
  );
}
