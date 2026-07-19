import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Filter, Plus, Search } from "lucide-react";
import {
  api,
  type Automation,
  type Lead,
  type LeadCreate,
  type LeadFacets,
  type LeadMetrics as LeadMetricsData,
} from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { LEADS_VIEW } from "@/lib/leads";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { LeadsTable } from "@/components/leads/LeadsTable";
import { LeadCreateDialog } from "@/components/leads/LeadCreateDialog";
import { LeadMetrics } from "@/components/leads/LeadMetrics";
import { FunnelStrip, type FunnelSegment } from "@/components/pipeline/FunnelStrip";

const PAGE_SIZE = 50;

// Build the funnel segments from the view config + live counts + bound sequences.
function buildSegments(
  metrics: LeadMetricsData | null,
  bound: Automation[],
): FunnelSegment[] {
  const countByStage = new Map(metrics?.stages.map((s) => [s.stage, s.count]) ?? []);
  const boundByStage = new Map<string, Automation>();
  for (const a of bound) {
    const stage = a.binding?.stage;
    if (typeof stage === "string") boundByStage.set(stage, a);
  }
  return LEADS_VIEW.stages.map((st) => {
    const hasSeq = LEADS_VIEW.sequenceStages.includes(st.key);
    const a = boundByStage.get(st.key);
    return {
      key: st.key,
      label: st.label,
      tone: st.tone,
      count: countByStage.get(st.key) ?? 0,
      sequence: hasSeq
        ? {
            state: a ? (a.status === "active" ? "active" : "paused") : "none",
            route: LEADS_VIEW.sequenceRoute(st.key),
          }
        : undefined,
    };
  });
}

export function LeadsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const status = searchParams.get("status") ?? "";
  const source = searchParams.get("source") ?? "";
  const q = searchParams.get("q") ?? "";

  const [leads, setLeads] = useState<Lead[]>([]);
  const [total, setTotal] = useState(0);
  const [facets, setFacets] = useState<LeadFacets>({ sources: [], regions: [] });
  const [metrics, setMetrics] = useState<LeadMetricsData | null>(null);
  const [bound, setBound] = useState<Automation[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [creating, setCreating] = useState(false);
  const [search, setSearch] = useState(q);

  const apiParams = {
    status: status || undefined,
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
    const page = await api.listLeads({ ...apiParams, limit: PAGE_SIZE, offset: 0 });
    setLeads(page.leads);
    setTotal(page.total);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, source, q]);

  const loadMetrics = useCallback(() => {
    api.getLeadMetrics().then(setMetrics).catch(() => {});
  }, []);
  const loadBound = useCallback(() => {
    api.listAutomations({ view: LEADS_VIEW.view }).then(setBound).catch(() => {});
  }, []);

  const loadRef = useRef({ loadFirst, loadMetrics, loadBound });
  loadRef.current = { loadFirst, loadMetrics, loadBound };

  // Facets + metrics + bound sequences once.
  useEffect(() => {
    api.getLeadFacets().then(setFacets).catch(() => {});
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

  // Realtime: leads changes refetch the directory + funnel counts; automations
  // changes refetch the bound-sequence chips (a sequence saved/activated elsewhere).
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refetchLeads = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        loadRef.current.loadFirst().catch(() => {});
        loadRef.current.loadMetrics();
      }, 250);
    };
    const channel = supabase
      .channel("leads-page")
      .on("postgres_changes", { event: "*", schema: "public", table: "leads" }, refetchLeads)
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
      const page = await api.listLeads({
        ...apiParams,
        limit: PAGE_SIZE,
        offset: leads.length,
      });
      setLeads((prev) => {
        const seen = new Set(prev.map((l) => l.id));
        return [...prev, ...page.leads.filter((l) => !seen.has(l.id))];
      });
      setTotal(page.total);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setLoadingMore(false);
    }
  };

  const onCreate = async (body: LeadCreate) => {
    try {
      await api.createLead(body);
      await loadFirst();
      loadMetrics();
      api.getLeadFacets().then(setFacets).catch(() => {});
      toast.success("Lead created");
    } catch (e) {
      toast.error(String(e));
      throw e;
    }
  };

  const hasMore = leads.length < total;
  const segments = buildSegments(metrics, bound);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Leads"
        description="Your sales pipeline — every prospective client and where they stand."
        action={
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" /> New lead
          </Button>
        }
      />

      <div className="flex min-h-0 flex-1 flex-col gap-4 p-4 sm:p-6">
        <LeadMetrics metrics={metrics} />

        <FunnelStrip
          segments={segments}
          active={status}
          onSelect={(key) => patchParam({ status: key || undefined })}
        />

        <div className="flex flex-wrap items-center gap-3">
          <Select
            className="w-44"
            value={source}
            onChange={(v) => patchParam({ source: v || undefined })}
            options={facets.sources.map((s) => ({ value: s, label: s }))}
            clearable
            placeholder="All sources"
            aria-label="Source filter"
          />
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
          ) : leads.length === 0 ? (
            <EmptyState
              icon={Filter}
              title="No leads here"
              description="Nothing matches these filters. Create a lead, or new inquiries will appear here as they arrive."
              action={
                <Button size="sm" variant="outline" onClick={() => setCreating(true)}>
                  <Plus className="h-4 w-4" /> New lead
                </Button>
              }
            />
          ) : (
            <div className="flex flex-col gap-3">
              <LeadsTable leads={leads} />
              <p className="text-xs text-muted-foreground">
                Showing {leads.length} of {total}
              </p>
              {hasMore && (
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

      <LeadCreateDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreate={onCreate}
        facets={facets}
      />
    </div>
  );
}
