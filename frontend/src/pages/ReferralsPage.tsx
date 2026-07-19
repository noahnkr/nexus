import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Handshake, Plus } from "lucide-react";
import { api, type ReferralMetrics, type ReferralSourceRow } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import {
  DEFAULT_SORT,
  sortSources,
  type SortDir,
  type SortKey,
} from "@/lib/referrals";
import { parseApiError } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { ReferralMetricsStrip } from "@/components/referrals/ReferralMetricsStrip";
import { MonthlyTrendBars } from "@/components/referrals/MonthlyTrendBars";
import { PartnerTable } from "@/components/referrals/PartnerTable";
import { PartnerDrawer } from "@/components/referrals/PartnerDrawer";
import { PartnerDialog, type PartnerForm } from "@/components/referrals/PartnerDialog";

interface DialogState {
  title: string;
  submitLabel: string;
  initial: Partial<PartnerForm>;
  editId: string | null; // null = create, else patch this partner
}

export function ReferralsPage() {
  const [metrics, setMetrics] = useState<ReferralMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>(DEFAULT_SORT);
  const [selected, setSelected] = useState<string | null>(null); // drawer source key
  const [dialog, setDialog] = useState<DialogState | null>(null);

  const loadMetrics = useCallback(async () => {
    const m = await api.getReferralMetrics();
    setMetrics(m);
  }, []);

  const loadRef = useRef(loadMetrics);
  loadRef.current = loadMetrics;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadMetrics()
      .catch((e) => !cancelled && toast.error(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [loadMetrics]);

  // Realtime: a partner change (Track / edit / delete) or a lead change moves the
  // numbers, so debounce-refetch the one metrics call. View-page precedent.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refetch = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => loadRef.current().catch(() => {}), 250);
    };
    const channel = supabase
      .channel("referrals-page")
      .on("postgres_changes", { event: "*", schema: "public", table: "referral_partners" }, refetch)
      .on("postgres_changes", { event: "*", schema: "public", table: "leads" }, refetch)
      .subscribe();
    return () => {
      if (timer) clearTimeout(timer);
      supabase.removeChannel(channel);
    };
  }, []);

  const sorted = useMemo(
    () => (metrics ? sortSources(metrics.sources, sort.key, sort.dir) : []),
    [metrics, sort],
  );

  const selectedRow = selected
    ? sorted.find((r) => r.source === selected) ?? null
    : null;

  // If the selected source vanished (e.g. an untracked partner with no leads was
  // deleted), close the drawer rather than dangle.
  useEffect(() => {
    if (selected && metrics && !metrics.sources.some((r) => r.source === selected)) {
      setSelected(null);
    }
  }, [selected, metrics]);

  const onSort = (key: SortKey) => {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "source" ? "asc" : "desc" },
    );
  };

  const openCreate = () =>
    setDialog({ title: "New referral partner", submitLabel: "Add partner", initial: {}, editId: null });

  const openTrack = (source: string) =>
    setDialog({
      title: "Track referral source",
      submitLabel: "Track partner",
      initial: { name: source },
      editId: null,
    });

  const openEdit = (row: ReferralSourceRow) => {
    if (!row.partner) return;
    setDialog({
      title: "Edit referral partner",
      submitLabel: "Save changes",
      initial: {
        name: row.source,
        category: row.partner.category,
        contact_name: row.partner.contact_name,
        phone: row.partner.phone,
        email: row.partner.email,
        notes: row.partner.notes,
      },
      editId: row.partner.id,
    });
  };

  const submitDialog = async (body: PartnerForm) => {
    const editId = dialog?.editId ?? null;
    try {
      if (editId) {
        await api.patchPartner(editId, body);
        toast.success("Partner updated");
      } else {
        await api.createPartner(body);
        // Follow the newly tracked source in the drawer.
        setSelected(body.name);
        toast.success("Partner tracked");
      }
      await loadMetrics();
    } catch (e) {
      const { status, detail } = parseApiError(e);
      toast.error(
        status === 409
          ? "A partner with that name already exists."
          : typeof detail === "string"
            ? detail
            : String(e),
      );
      throw e; // keep the dialog open on failure
    }
  };

  const deletePartner = async (partnerId: string) => {
    try {
      await api.deletePartner(partnerId);
      await loadMetrics();
      toast.success("Stopped tracking partner");
    } catch (e) {
      toast.error(String(e));
      throw e;
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Referrals"
        description="Which partners send leads that convert — and how much business each brings in."
        action={
          <Button size="sm" onClick={openCreate}>
            <Plus className="h-4 w-4" /> New partner
          </Button>
        }
      />

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4 sm:p-6">
        <ReferralMetricsStrip totals={metrics?.totals ?? null} />

        {metrics && metrics.monthly.length > 0 && (
          <div className="rounded-xl border bg-card p-4 shadow-sm">
            <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Lead volume (last {metrics.months.length} months)
            </div>
            <MonthlyTrendBars monthly={metrics.monthly} months={metrics.months} />
          </div>
        )}

        {loading ? (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : sorted.length === 0 ? (
          <EmptyState
            icon={Handshake}
            title="No referral sources yet"
            description="As leads come in with a source, they'll appear here. Track a source to record its category and contact."
            action={
              <Button size="sm" variant="outline" onClick={openCreate}>
                <Plus className="h-4 w-4" /> New partner
              </Button>
            }
          />
        ) : (
          <PartnerTable
            rows={sorted}
            months={metrics?.months ?? []}
            sort={sort}
            onSort={onSort}
            onSelectRow={(row) => setSelected(row.source)}
            onTrack={openTrack}
          />
        )}
      </div>

      {selectedRow && (
        <PartnerDrawer
          row={selectedRow}
          months={metrics?.months ?? []}
          onClose={() => setSelected(null)}
          onEdit={openEdit}
          onTrack={openTrack}
          onDelete={deletePartner}
        />
      )}

      {dialog && (
        <PartnerDialog
          open
          title={dialog.title}
          submitLabel={dialog.submitLabel}
          initial={dialog.initial}
          onSubmit={submitDialog}
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}
