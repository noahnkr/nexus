import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { HeartPulse, Plus, Search } from "lucide-react";
import {
  api,
  type CensusMetrics,
  type Client,
  type ClientCreate,
  type ClientFacets,
} from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { CLIENT_STATUSES, statusMeta } from "@/lib/clients";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { CensusStrip } from "@/components/clients/CensusStrip";
import { ClientsTable } from "@/components/clients/ClientsTable";
import { ClientCreateDialog } from "@/components/clients/ClientCreateDialog";
import { PAYER_LABELS, PAYERS } from "@/lib/clients";
import type { Payer } from "@/lib/api";

const PAGE_SIZE = 50;

// Status filter chips, built from the status meta so labels/order/tones can't
// drift from the seam. Clicking a chip toggles the `status` URL param.
function StatusChips({
  active,
  onSelect,
}: {
  active: string;
  onSelect: (status: string | undefined) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {CLIENT_STATUSES.map((s) => {
        const meta = statusMeta(s);
        const on = active === s;
        return (
          <button
            key={s}
            type="button"
            onClick={() => onSelect(on ? undefined : s)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors",
              on
                ? "border-primary bg-primary/10 text-primary"
                : "border-input text-muted-foreground hover:border-primary/40 hover:text-foreground",
            )}
          >
            <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} />
            {meta.label}
          </button>
        );
      })}
    </div>
  );
}

export function ClientsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const status = searchParams.get("status") ?? "";
  const payer = searchParams.get("payer") ?? "";
  const regionId = searchParams.get("region_id") ?? "";
  const q = searchParams.get("q") ?? "";

  const [clients, setClients] = useState<Client[]>([]);
  const [total, setTotal] = useState(0);
  const [facets, setFacets] = useState<ClientFacets>({
    statuses: [],
    payers: [],
    regions: [],
  });
  const [metrics, setMetrics] = useState<CensusMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [creating, setCreating] = useState(false);
  const [search, setSearch] = useState(q);

  const apiParams = {
    status: status || undefined,
    payer: payer || undefined,
    region_id: regionId || undefined,
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
    const page = await api.listClients({ ...apiParams, limit: PAGE_SIZE, offset: 0 });
    setClients(page.clients);
    setTotal(page.total);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, payer, regionId, q]);

  const loadMetrics = useCallback(() => {
    api.getClientMetrics().then(setMetrics).catch(() => {});
  }, []);

  const loadRef = useRef({ loadFirst, loadMetrics });
  loadRef.current = { loadFirst, loadMetrics };

  // Facets + census once.
  useEffect(() => {
    api.getClientFacets().then(setFacets).catch(() => {});
    loadMetrics();
  }, [loadMetrics]);

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

  // Realtime: a client change refetches the directory + census; a schedule change
  // refetches only the census (delivered/scheduled/open hours move as visits do).
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refetch = (withList: boolean) => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        if (withList) loadRef.current.loadFirst().catch(() => {});
        loadRef.current.loadMetrics();
      }, 250);
    };
    const channel = supabase
      .channel("clients-page")
      .on("postgres_changes", { event: "*", schema: "public", table: "clients" }, () =>
        refetch(true),
      )
      .on("postgres_changes", { event: "*", schema: "public", table: "schedules" }, () =>
        refetch(false),
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
      const page = await api.listClients({
        ...apiParams,
        limit: PAGE_SIZE,
        offset: clients.length,
      });
      setClients((prev) => {
        const seen = new Set(prev.map((c) => c.id));
        return [...prev, ...page.clients.filter((c) => !seen.has(c.id))];
      });
      setTotal(page.total);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setLoadingMore(false);
    }
  };

  const onCreate = async (body: ClientCreate) => {
    try {
      await api.createClient(body);
      await loadFirst();
      loadMetrics();
      api.getClientFacets().then(setFacets).catch(() => {});
      toast.success("Client created");
    } catch (e) {
      toast.error(String(e));
      throw e;
    }
  };

  const hasMore = clients.length < total;

  // Payer filter options: the observed facet keys, plain-labelled. Fall back to the
  // full payer vocabulary if the facet is empty (fresh tenant).
  const payerOptions = (facets.payers.length ? facets.payers : PAYERS).map((p) => ({
    value: p,
    label: PAYER_LABELS[p as Payer] ?? p,
  }));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Clients"
        description="Everyone you serve — their status, care, and hours delivered."
        action={
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" /> New client
          </Button>
        }
      />

      <div className="flex min-h-0 flex-1 flex-col gap-4 p-4 sm:p-6">
        <CensusStrip
          metrics={metrics}
          activePayer={payer}
          activeRegion={regionId}
          onFilterPayer={(v) => patchParam({ payer: v })}
          onFilterRegion={(v) => patchParam({ region_id: v })}
        />

        <StatusChips active={status} onSelect={(v) => patchParam({ status: v })} />

        <div className="flex flex-wrap items-center gap-3">
          <Select
            className="w-44"
            value={payer}
            onChange={(v) => patchParam({ payer: v || undefined })}
            options={payerOptions}
            clearable
            placeholder="All payers"
            aria-label="Payer filter"
          />
          <Select
            className="w-44"
            value={regionId}
            onChange={(v) => patchParam({ region_id: v || undefined })}
            options={facets.regions.map((r) => ({ value: r.id, label: r.name }))}
            clearable
            placeholder="All regions"
            aria-label="Region filter"
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
          ) : clients.length === 0 ? (
            <EmptyState
              icon={HeartPulse}
              title="No clients here"
              description="Nothing matches these filters. Create a client, or new clients will appear here as leads convert."
              action={
                <Button size="sm" variant="outline" onClick={() => setCreating(true)}>
                  <Plus className="h-4 w-4" /> New client
                </Button>
              }
            />
          ) : (
            <div className="flex flex-col gap-3">
              <ClientsTable clients={clients} />
              <p className="text-xs text-muted-foreground">
                Showing {clients.length} of {total}
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

      <ClientCreateDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreate={onCreate}
        facets={facets}
      />
    </div>
  );
}
