import { cn } from "@/lib/utils";

// Status tabs map to the API's comma-separated `status` param. "Open" is the
// default working view (pending + in_progress); "All" clears the status filter.
export const STATUS_TABS = [
  { key: "open", label: "Open", status: "pending,in_progress" },
  { key: "done", label: "Done", status: "done" },
  { key: "cancelled", label: "Cancelled", status: "cancelled" },
  { key: "all", label: "All", status: "" },
] as const;

const PRIORITIES = ["low", "normal", "high", "urgent"] as const;

const selectClass =
  "h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

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

      <select
        className={selectClass}
        value={priority}
        onChange={(e) => onPriorityChange(e.target.value)}
      >
        <option value="">All priorities</option>
        {PRIORITIES.map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>
    </div>
  );
}
