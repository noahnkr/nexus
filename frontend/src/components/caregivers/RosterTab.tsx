import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Search, Users } from "lucide-react";
import { api, type ResourceStatus, type RosterCaregiver, type WorkforceRoster } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { RESOURCE_STATUSES, filterRoster, resourceStatusMeta, sortRoster } from "@/lib/workforce";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import { ComplianceStrip } from "@/components/caregivers/ComplianceStrip";
import { RosterTable } from "@/components/caregivers/RosterTable";
import { CaregiverDrawer } from "@/components/schedule/CaregiverDrawer";

// The working roster: who is on staff, how booked they are, and whose credentials
// need attention. One call to /api/workforce/roster carries both the metrics and
// the rows — every derived number (utilization, credential status) is computed
// server-side, so nothing on this page does compliance or capacity math.
//
// Search and the status filter are client-side: the roster is low tens of people,
// so a server round trip per keystroke would be pure latency.
export function RosterTab() {
  const [data, setData] = useState<WorkforceRoster | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<ResourceStatus | "">("");
  const [openId, setOpenId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setData(await api.getWorkforceRoster());
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

  // Realtime: a credential added in the drawer (or a caregiver deactivated
  // anywhere) moves the compliance strip without a manual refresh. Debounced —
  // one save can produce several row events.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refetch = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => loadRef.current().catch(() => {}), 250);
    };
    const channel = supabase
      .channel("roster-tab")
      .on("postgres_changes", { event: "*", schema: "public", table: "resources" }, refetch)
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "resource_credentials" },
        refetch,
      )
      .subscribe();
    return () => {
      if (timer) clearTimeout(timer);
      supabase.removeChannel(channel);
    };
  }, []);

  const all = data?.caregivers ?? [];
  const rows = sortRoster(filterRoster(all, { search, status: status || undefined }));
  const open: RosterCaregiver | null = all.find((c) => c.id === openId) ?? null;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-4 sm:p-6">
      <ComplianceStrip metrics={data?.metrics ?? null} />

      <div className="flex flex-wrap items-center gap-3">
        <Select
          className="w-40"
          value={status}
          onChange={(v) => setStatus(v)}
          options={RESOURCE_STATUSES.map((s) => ({
            value: s,
            label: resourceStatusMeta(s).label,
            dot: resourceStatusMeta(s).dot,
          }))}
          clearable
          placeholder="All statuses"
          aria-label="Status filter"
        />
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search name, phone, credential"
            className="w-72 pl-8"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Users}
            title={all.length === 0 ? "No caregivers yet" : "No caregivers here"}
            description={
              all.length === 0
                ? "Hire an applicant from the Pipeline tab and they'll appear on the roster."
                : "Nothing matches this search and filter."
            }
          />
        ) : (
          <div className="flex flex-col gap-3">
            <RosterTable caregivers={rows} onOpen={(c) => setOpenId(c.id)} />
            <p className="text-xs text-muted-foreground">
              Showing {rows.length} of {all.length}
            </p>
          </div>
        )}
      </div>

      {open && (
        <CaregiverDrawer
          caregiver={open}
          onClose={() => setOpenId(null)}
          onSaved={load}
        />
      )}
    </div>
  );
}
