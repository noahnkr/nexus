import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Plus, Search, Users } from "lucide-react";
import {
  api,
  type Applicant,
  type ApplicantCreate,
  type ApplicantFacets,
  type ApplicantMetrics as ApplicantMetricsData,
  type Automation,
} from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { CAREGIVERS_VIEW } from "@/lib/caregivers";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { ApplicantsTable } from "@/components/caregivers/ApplicantsTable";
import { ApplicantCreateDialog } from "@/components/caregivers/ApplicantCreateDialog";
import { HiringMetrics } from "@/components/caregivers/HiringMetrics";
import { FunnelStrip, type FunnelSegment } from "@/components/pipeline/FunnelStrip";

const PAGE_SIZE = 50;

const selectClass =
  "h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

// Build the funnel segments from the view config + live counts + bound sequences.
function buildSegments(
  metrics: ApplicantMetricsData | null,
  bound: Automation[],
): FunnelSegment[] {
  const countByStage = new Map(metrics?.stages.map((s) => [s.stage, s.count]) ?? []);
  const boundByStage = new Map<string, Automation>();
  for (const a of bound) {
    const stage = a.binding?.stage;
    if (typeof stage === "string") boundByStage.set(stage, a);
  }
  return CAREGIVERS_VIEW.stages.map((st) => {
    const hasSeq = CAREGIVERS_VIEW.sequenceStages.includes(st.key);
    const a = boundByStage.get(st.key);
    return {
      key: st.key,
      label: st.label,
      tone: st.tone,
      count: countByStage.get(st.key) ?? 0,
      sequence: hasSeq
        ? {
            state: a ? (a.status === "active" ? "active" : "paused") : "none",
            route: CAREGIVERS_VIEW.sequenceRoute(st.key),
          }
        : undefined,
    };
  });
}

export function CaregiversPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const stage = searchParams.get("stage") ?? "";
  const source = searchParams.get("source") ?? "";
  const q = searchParams.get("q") ?? "";

  const [applicants, setApplicants] = useState<Applicant[]>([]);
  const [total, setTotal] = useState(0);
  const [facets, setFacets] = useState<ApplicantFacets>({
    sources: [],
    regions: [],
    qualifications: [],
  });
  const [metrics, setMetrics] = useState<ApplicantMetricsData | null>(null);
  const [bound, setBound] = useState<Automation[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [creating, setCreating] = useState(false);
  const [search, setSearch] = useState(q);

  const apiParams = {
    stage: stage || undefined,
    source: source || undefined,
    q: q || undefined,
  };

  const patchParam = useCallback(
    (patch: Record<string, string | undefined>) => {
      const next = new URLSearchParams(searchParams);
      for (const [k, v] of Object.entries(patch)) {
        if (v) next.set(k, v);
        else next.delete(k);
      }
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  // Debounce the search box into the `q` URL param.
  useEffect(() => {
    if (search === q) return;
    const t = setTimeout(() => patchParam({ q: search || undefined }), 300);
    return () => clearTimeout(t);
  }, [search, q, patchParam]);

  const loadFirst = useCallback(async () => {
    const page = await api.listApplicants({ ...apiParams, limit: PAGE_SIZE, offset: 0 });
    setApplicants(page.applicants);
    setTotal(page.total);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, source, q]);

  const loadMetrics = useCallback(() => {
    api.getApplicantMetrics().then(setMetrics).catch(() => {});
  }, []);
  const loadBound = useCallback(() => {
    api.listAutomations({ view: CAREGIVERS_VIEW.view }).then(setBound).catch(() => {});
  }, []);

  const loadRef = useRef({ loadFirst, loadMetrics, loadBound });
  loadRef.current = { loadFirst, loadMetrics, loadBound };

  // Facets + metrics + bound sequences once.
  useEffect(() => {
    api.getApplicantFacets().then(setFacets).catch(() => {});
    loadMetrics();
    loadBound();
  }, [loadMetrics, loadBound]);

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

  // Realtime: applicants changes refetch the directory + funnel counts; automations
  // changes refetch the bound-sequence chips (a sequence saved/activated elsewhere).
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refetch = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        loadRef.current.loadFirst().catch(() => {});
        loadRef.current.loadMetrics();
      }, 250);
    };
    const channel = supabase
      .channel("caregivers-page")
      .on("postgres_changes", { event: "*", schema: "public", table: "applicants" }, refetch)
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "automations" },
        () => loadRef.current.loadBound(),
      )
      .subscribe();
    return () => {
      if (timer) clearTimeout(timer);
      supabase.removeChannel(channel);
    };
  }, []);

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const page = await api.listApplicants({
        ...apiParams,
        limit: PAGE_SIZE,
        offset: applicants.length,
      });
      setApplicants((prev) => {
        const seen = new Set(prev.map((a) => a.id));
        return [...prev, ...page.applicants.filter((a) => !seen.has(a.id))];
      });
      setTotal(page.total);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setLoadingMore(false);
    }
  };

  const onCreate = async (body: ApplicantCreate) => {
    try {
      await api.createApplicant(body);
      await loadFirst();
      loadMetrics();
      api.getApplicantFacets().then(setFacets).catch(() => {});
      toast.success("Applicant created");
    } catch (e) {
      toast.error(String(e));
      throw e;
    }
  };

  const hasMore = applicants.length < total;
  const segments = buildSegments(metrics, bound);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Caregivers"
        description="Your hiring pipeline — every applicant and where they stand."
        action={
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" /> New applicant
          </Button>
        }
      />

      <div className="flex min-h-0 flex-1 flex-col gap-4 p-6">
        <HiringMetrics metrics={metrics} />

        <FunnelStrip
          segments={segments}
          active={stage}
          onSelect={(key) => patchParam({ stage: key || undefined })}
        />

        <div className="flex flex-wrap items-center gap-3">
          <select
            className={selectClass}
            value={source}
            onChange={(e) => patchParam({ source: e.target.value || undefined })}
          >
            <option value="">All sources</option>
            {facets.sources.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name, phone, email"
              className="w-64 pl-8"
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
          ) : applicants.length === 0 ? (
            <EmptyState
              icon={Users}
              title="No applicants here"
              description="Nothing matches these filters. Create an applicant, or new candidates will appear here as they apply."
              action={
                <Button size="sm" variant="outline" onClick={() => setCreating(true)}>
                  <Plus className="h-4 w-4" /> New applicant
                </Button>
              }
            />
          ) : (
            <div className="flex flex-col gap-3">
              <ApplicantsTable applicants={applicants} />
              <p className="text-xs text-muted-foreground">
                Showing {applicants.length} of {total}
              </p>
              {hasMore && (
                <div className="flex justify-center py-2">
                  <Button variant="outline" size="sm" onClick={loadMore} disabled={loadingMore}>
                    {loadingMore ? "Loading…" : "Load more"}
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <ApplicantCreateDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreate={onCreate}
        facets={facets}
      />
    </div>
  );
}
