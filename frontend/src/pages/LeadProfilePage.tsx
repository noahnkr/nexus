import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ArrowLeft } from "lucide-react";
import { api, type Lead, type LeadFacets, type LeadPatch } from "@/lib/api";
import { parseApiError } from "@/lib/utils";
import { stageLabel, stageTone } from "@/lib/leads";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { Filter } from "lucide-react";
import { SmartSummary } from "@/components/leads/SmartSummary";
import { LeadInfoCard } from "@/components/leads/LeadInfoCard";
import { EntityTimeline } from "@/components/events/EntityTimeline";

export function LeadProfilePage() {
  const { id = "" } = useParams();
  const [lead, setLead] = useState<Lead | null>(null);
  const [facets, setFacets] = useState<LeadFacets>({ sources: [], regions: [] });
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [busy, setBusy] = useState(false);
  const [timelineKey, setTimelineKey] = useState(0);

  const load = useCallback(async () => {
    try {
      setLead(await api.getLead(id));
    } catch (e) {
      const { status } = parseApiError(e);
      if (status === 404) setNotFound(true);
      else toast.error(String(e));
    }
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setNotFound(false);
    load().finally(() => !cancelled && setLoading(false));
    api.getLeadFacets().then(setFacets).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [load]);

  // A write (field edit or stage move) PATCHes only the changed fields, then
  // refreshes the lead and bumps the timeline so the new event shows immediately.
  const onPatch = async (patch: LeadPatch) => {
    setBusy(true);
    try {
      const updated = await api.patchLead(id, patch);
      setLead(updated);
      setTimelineKey((k) => k + 1);
      if (patch.status) toast.success(`Moved to ${stageLabel(patch.status)}`);
    } catch (e) {
      const { detail } = parseApiError(e);
      toast.error(typeof detail === "string" ? detail : "Update failed");
      await load(); // resync UI to the server's truth on failure
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <PageHeader title="Lead" />
        <div className="flex flex-col gap-4 p-6">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      </div>
    );
  }

  if (notFound || !lead) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <PageHeader title="Lead" />
        <div className="p-6">
          <EmptyState
            icon={Filter}
            title="Lead not found"
            description="This lead doesn't exist or isn't visible to you."
            action={
              <Link to="/leads" className="text-sm text-primary hover:underline">
                Back to leads
              </Link>
            }
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title={lead.name}
        description={lead.email ?? lead.phone ?? undefined}
        action={
          <Badge variant={stageTone(lead.status)}>{stageLabel(lead.status)}</Badge>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-4 p-6">
          <Link
            to="/leads"
            className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" /> All leads
          </Link>

          <SmartSummary leadId={lead.id} />

          <LeadInfoCard lead={lead} facets={facets} onPatch={onPatch} busy={busy} />

          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Timeline
            </p>
            <div className="rounded-lg border">
              <EntityTimeline
                entityType="lead"
                entityId={lead.id}
                refreshKey={timelineKey}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
