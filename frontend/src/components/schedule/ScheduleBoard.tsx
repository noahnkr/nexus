import { Fragment } from "react";
import { CalendarClock } from "lucide-react";
import type { CaregiverRoster, ScheduleBoard as Board, ScheduleVisit } from "@/lib/api";
import { dayColumns, visitDayIso } from "@/lib/schedule";
import { cn } from "@/lib/utils";
import { VisitBlock } from "./VisitBlock";

// Week grid: a sticky name column + Mon–Sun day columns. A pinned warning-toned
// "Open shifts" row (shown only when the week has open visits) sits above one row
// per caregiver, alphabetical. Cells stack VisitBlock chips sorted by start time —
// day-column placement, not hour geometry. The whole grid scrolls horizontally
// inside its own container so the page body never scrolls sideways.
const GRID_COLS = "180px repeat(7, minmax(120px, 1fr))";

function dayCells(
  visits: ScheduleVisit[],
  columns: ReturnType<typeof dayColumns>,
  onVisitClick: (v: ScheduleVisit) => void,
) {
  return columns.map((c) => {
    const here = visits
      .filter((v) => visitDayIso(v) === c.iso)
      .sort((a, b) => a.start_time.localeCompare(b.start_time));
    return (
      <div
        key={c.iso}
        className={cn(
          "min-h-[64px] space-y-1 border-b border-r p-1.5",
          c.isToday && "bg-primary/[0.03]",
        )}
      >
        {here.map((v) => (
          <VisitBlock key={v.id} visit={v} onClick={() => onVisitClick(v)} />
        ))}
      </div>
    );
  });
}

export function ScheduleBoard({
  board,
  onVisitClick,
  onCaregiverClick,
}: {
  board: Board;
  onVisitClick: (v: ScheduleVisit) => void;
  onCaregiverClick: (c: CaregiverRoster) => void;
}) {
  const columns = dayColumns(board.week_start);
  const openVisits = board.visits.filter((v) => v.resource_id === null);
  const caregivers = [...board.caregivers].sort((a, b) => a.name.localeCompare(b.name));
  const byResource = new Map<string, ScheduleVisit[]>();
  for (const v of board.visits) {
    if (v.resource_id) {
      const list = byResource.get(v.resource_id) ?? [];
      list.push(v);
      byResource.set(v.resource_id, list);
    }
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto rounded-lg border">
      <div className="grid min-w-[900px]" style={{ gridTemplateColumns: GRID_COLS }}>
        {/* Header row */}
        <div className="sticky left-0 top-0 z-20 border-b border-r bg-muted/70 px-3 py-2 text-xs font-semibold backdrop-blur">
          Caregiver
        </div>
        {columns.map((c) => (
          <div
            key={c.iso}
            className={cn(
              "sticky top-0 z-10 border-b border-r bg-muted/70 px-2 py-2 text-center backdrop-blur",
              c.isToday && "text-primary",
            )}
          >
            <div className="text-xs font-semibold">{c.weekday}</div>
            <div className="text-[10px] text-muted-foreground">{c.label}</div>
          </div>
        ))}

        {/* Pinned Open shifts row */}
        {openVisits.length > 0 && (
          <Fragment>
            <div className="sticky left-0 z-10 flex items-center gap-2 border-b border-r bg-warning/10 px-3 py-2">
              <CalendarClock className="h-4 w-4 shrink-0 text-warning" />
              <div className="min-w-0">
                <div className="truncate text-[13px] font-semibold text-warning">
                  Open shifts
                </div>
                <div className="text-[11px] text-muted-foreground">
                  {openVisits.length} unfilled
                </div>
              </div>
            </div>
            {dayCells(openVisits, columns, onVisitClick)}
          </Fragment>
        )}

        {/* Caregiver rows */}
        {caregivers.map((cg) => (
          <Fragment key={cg.id}>
            <button
              type="button"
              onClick={() => onCaregiverClick(cg)}
              className="sticky left-0 z-10 flex flex-col items-start justify-center border-b border-r bg-card px-3 py-2 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            >
              <span className="truncate text-[13px] font-medium">{cg.name}</span>
              <span className="text-[11px] text-muted-foreground tabular-nums">
                {cg.hours_this_week}h this week
              </span>
            </button>
            {dayCells(byResource.get(cg.id) ?? [], columns, onVisitClick)}
          </Fragment>
        ))}
      </div>
    </div>
  );
}
