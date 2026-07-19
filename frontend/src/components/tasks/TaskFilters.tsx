import { cn } from "@/lib/utils";
import { Select, type SelectOption } from "@/components/ui/Select";
import { PRIORITY_DOT } from "./TaskCard";
import type { TaskPriority } from "@/lib/api";

// Status tabs map to the API's comma-separated `status` param. "Open" is the
// default working view (pending + in_progress); "All" clears the status filter.
export const STATUS_TABS = [
  { key: "open", label: "Open", status: "pending,in_progress" },
  { key: "done", label: "Done", status: "done" },
  { key: "cancelled", label: "Cancelled", status: "cancelled" },
  { key: "all", label: "All", status: "" },
] as const;

const PRIORITIES: TaskPriority[] = ["low", "normal", "high", "urgent"];

const PRIORITY_OPTIONS: SelectOption[] = PRIORITIES.map((p) => ({
  value: p,
  label: p,
  dot: PRIORITY_DOT[p],
}));

export function TaskFilters({
  status,
  priority,
  onStatusChange,
  onPriorityChange,
}: {
  status: string; // the raw comma-separated status value ("" = All)
  priority: string;
  onStatusChange: (status: string) => void;
  onPriorityChange: (priority: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="inline-flex rounded-md border p-0.5">
        {STATUS_TABS.map((t) => {
          const active = t.status === status;
          return (
            <button
              key={t.key}
              onClick={() => onStatusChange(t.status)}
              className={cn(
                "rounded px-3 py-1 text-sm font-medium transition-colors",
                active
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      <Select
        className="w-44"
        value={priority}
        onChange={onPriorityChange}
        options={PRIORITY_OPTIONS}
        clearable
        placeholder="All priorities"
        aria-label="Priority filter"
      />
    </div>
  );
}
