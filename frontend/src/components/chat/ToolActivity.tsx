import { Link } from "react-router-dom";
import { Check, Clock, Loader2, X, Wrench } from "lucide-react";
import { cn } from "@/lib/utils";

export interface UITool {
  id: string;
  label: string;
  status: "running" | "done" | "error" | "queued";
}

// A muted chip row summarizing the tools the agent used this turn. Plain-language
// labels only — never raw JSON, SQL, or tool payloads (those live in the Event
// Log and LangSmith). A "queued" chip means the action awaits approval on /tasks.
export function ToolActivity({ tools }: { tools?: UITool[] }) {
  if (!tools || tools.length === 0) return null;
  return (
    <div className="mb-2 flex flex-wrap gap-1.5">
      {tools.map((t) => {
        const chip = (
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
              t.status === "error"
                ? "border-destructive/40 text-destructive"
                : t.status === "queued"
                  ? "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400"
                  : "border-border text-muted-foreground",
            )}
          >
            {t.status === "running" ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : t.status === "error" ? (
              <X className="h-3 w-3" />
            ) : t.status === "queued" ? (
              <Clock className="h-3 w-3" />
            ) : (
              <Check className="h-3 w-3" />
            )}
            {t.status !== "queued" && (
              <Wrench className="h-3 w-3 shrink-0 opacity-60" />
            )}
            <span className="truncate">{t.label}</span>
          </span>
        );
        // Queued chips link to the Tasks page where the approval waits.
        return t.status === "queued" ? (
          <Link key={t.id} to="/tasks" title="Review in Tasks">
            {chip}
          </Link>
        ) : (
          <span key={t.id}>{chip}</span>
        );
      })}
    </div>
  );
}
