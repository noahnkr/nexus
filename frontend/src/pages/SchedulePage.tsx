import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { CalendarDays, ChevronLeft, ChevronRight, Plus, Users } from "lucide-react";
import {
  api,
  type CaregiverRoster,
  type ScheduleBoard as Board,
  type ScheduleCreate,
  type ScheduleVisit,
} from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { addWeeks, todayIso, weekLabel, weekStartOf } from "@/lib/schedule";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { ScheduleBoard } from "@/components/schedule/ScheduleBoard";
import { VisitDrawer } from "@/components/schedule/VisitDrawer";
import { CaregiverDrawer } from "@/components/schedule/CaregiverDrawer";
import { VisitCreateDialog } from "@/components/schedule/VisitCreateDialog";

// The /schedule week board. One getScheduleWeek fetch feeds the whole board;
// Supabase Realtime on `schedules` debounce-refetches (the M8 precedent). The week
// round-trips to ?week=YYYY-MM-DD (Monday-normalized) like the M9 filter↔URL.
export function SchedulePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const weekParam = searchParams.get("week");
  const weekStart = weekStartOf(weekParam || todayIso());

  const [board, setBoard] = useState<Board | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [selectedVisitId, setSelectedVisitId] = useState<string | null>(null);
  const [selectedCaregiver, setSelectedCaregiver] = useState<CaregiverRoster | null>(null);

  const setWeek = useCallback(
    (next: string) => {
      const params = new URLSearchParams(searchParams);
      params.set("week", next);
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const load = useCallback(async () => {
    const data = await api.getScheduleWeek(weekStart);
    setBoard(data);
  }, [weekStart]);

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

  // Realtime: any schedules change refetches the current week (debounced).
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refetch = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => loadRef.current().catch(() => {}), 250);
    };
    const channel = supabase
      .channel("schedule-page")
      .on("postgres_changes", { event: "*", schema: "public", table: "schedules" }, refetch)
      .subscribe();
    return () => {
      if (timer) clearTimeout(timer);
      supabase.removeChannel(channel);
    };
  }, []);

  const refresh = useCallback(async () => {
    await loadRef.current();
  }, []);

  const onCreate = async (body: ScheduleCreate) => {
    const res = await api.createVisits(body);
    await refresh();
    toast.success(`Created ${res.visits.length} visit${res.visits.length === 1 ? "" : "s"}`);
  };

  const openVisit = (v: ScheduleVisit) => {
    setSelectedCaregiver(null);
    setSelectedVisitId(v.id);
  };
  const openCaregiver = (c: CaregiverRoster) => {
    setSelectedVisitId(null);
    setSelectedCaregiver(c);
  };

  const rosterEmpty = board !== null && board.caregivers.length === 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Schedule"
        description="The week's visits — open shifts, caregivers, and call-out coverage."
        action={
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" /> New visit
          </Button>
        }
      />

      <div className="flex min-h-0 flex-1 flex-col gap-4 p-6">
        {/* Week navigation */}
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="icon"
            aria-label="Previous week"
            onClick={() => setWeek(addWeeks(weekStart, -1))}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button variant="outline" size="sm" onClick={() => setWeek(weekStartOf(todayIso()))}>
            This week
          </Button>
          <Button
            variant="outline"
            size="icon"
            aria-label="Next week"
            onClick={() => setWeek(addWeeks(weekStart, 1))}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
          <span className="ml-1 text-sm font-medium text-muted-foreground">
            {weekLabel(weekStart)}
          </span>
        </div>

        {loading && !board ? (
          <Skeleton className="min-h-0 flex-1" />
        ) : rosterEmpty ? (
          <EmptyState
            icon={Users}
            title="No caregivers yet"
            description="Hire a caregiver from the Caregivers pipeline and they'll appear here to schedule."
          />
        ) : board ? (
          <ScheduleBoard
            board={board}
            onVisitClick={openVisit}
            onCaregiverClick={openCaregiver}
          />
        ) : (
          <EmptyState
            icon={CalendarDays}
            title="Nothing to show"
            description="Couldn't load the schedule for this week."
          />
        )}
      </div>

      {board && selectedVisitId && (
        <VisitDrawer
          board={board}
          visitId={selectedVisitId}
          onClose={() => setSelectedVisitId(null)}
          onRefresh={refresh}
          onSelectVisit={setSelectedVisitId}
        />
      )}

      {selectedCaregiver && (
        <CaregiverDrawer
          caregiver={selectedCaregiver}
          onClose={() => setSelectedCaregiver(null)}
          onSaved={refresh}
        />
      )}

      <VisitCreateDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreate={onCreate}
        clients={board?.clients ?? []}
        caregivers={board?.caregivers ?? []}
      />
    </div>
  );
}
