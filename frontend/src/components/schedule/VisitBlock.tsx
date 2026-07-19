import type { ScheduleVisit } from "@/lib/api";
import { formatRange, statusMeta } from "@/lib/schedule";
import { cn } from "@/lib/utils";

// One visit chip in a board cell: time range + client name, tinted by status. A
// replacement (covering a call-out) carries a small "covering" hint. Day-column
// placement only — no hour-scaled geometry (visits are 2–10h; a time axis is
// complexity the board doesn't need). Clicking opens the visit drawer.
export function VisitBlock({
  visit,
  onClick,
}: {
  visit: ScheduleVisit;
  onClick: () => void;
}) {
  const meta = statusMeta(visit.status);
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full rounded-md border px-2 py-1 text-left text-[11px] leading-tight transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        meta.block,
      )}
      title={`${formatRange(visit)} · ${visit.client_name} · ${meta.label}`}
    >
      <div className="font-medium tabular-nums">{formatRange(visit)}</div>
      <div className="truncate">{visit.client_name}</div>
      {visit.replaces_schedule_id && (
        <div className="mt-0.5 text-[10px] font-medium uppercase tracking-wide opacity-80">
          Covering
        </div>
      )}
    </button>
  );
}
