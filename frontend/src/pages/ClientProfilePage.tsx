import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ArrowLeft, HeartPulse } from "lucide-react";
import { api, type ClientDetail, type ClientFacets, type ClientPatch } from "@/lib/api";
import { parseApiError } from "@/lib/utils";
import { statusMeta } from "@/lib/clients";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { SmartSummary } from "@/components/leads/SmartSummary";
import { EntityTimeline } from "@/components/events/EntityTimeline";
import { ClientInfoCard } from "@/components/clients/ClientInfoCard";
import { CareCard } from "@/components/clients/CareCard";
import { HoursCard } from "@/components/clients/HoursCard";
import { ContactsCard } from "@/components/clients/ContactsCard";
import { CaregiversCard } from "@/components/clients/CaregiversCard";
import { ClientVisitsCard } from "@/components/clients/ClientVisitsCard";
import { ClientDocumentsCard } from "@/components/clients/ClientDocumentsCard";

export function ClientProfilePage() {
  const { id = "" } = useParams();
  const [client, setClient] = useState<ClientDetail | null>(null);
  const [facets, setFacets] = useState<ClientFacets>({
    statuses: [],
    payers: [],
    regions: [],
  });
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [busy, setBusy] = useState(false);
  const [timelineKey, setTimelineKey] = useState(0);

  const load = useCallback(async () => {
    try {
      setClient(await api.getClient(id));
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
    api.getClientFacets().then(setFacets).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [load]);

  // A write PATCHes only the changed fields, then refreshes the client and bumps
  // the timeline so the new event shows at once.
  const onPatch = async (patch: ClientPatch) => {
    setBusy(true);
    try {
      const updated = await api.patchClient(id, patch);
      setClient(updated);
      setTimelineKey((k) => k + 1);
      if (patch.status) toast.success(`Status updated to ${statusMeta(patch.status).label}`);
    } catch (e) {
      const { detail } = parseApiError(e);
      toast.error(typeof detail === "string" ? detail : "Update failed");
      await load(); // resync to the server's truth on failure
    } finally {
      setBusy(false);
    }
  };

  // Contact CRUD refreshes the client (contacts + a client.updated event).
  const refreshAfterContacts = async () => {
    await load();
    setTimelineKey((k) => k + 1);
  };

  if (loading) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <PageHeader title="Client" />
        <div className="flex flex-col gap-4 p-4 sm:p-6">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      </div>
    );
  }

  if (notFound || !client) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <PageHeader title="Client" />
        <div className="p-6">
          <EmptyState
            icon={HeartPulse}
            title="Client not found"
            description="This client doesn't exist or isn't visible to you."
            action={
              <Link to="/clients" className="text-sm text-primary hover:underline">
                Back to clients
              </Link>
            }
          />
        </div>
      </div>
    );
  }

  const meta = statusMeta(client.status);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title={client.name}
        description={client.phone ?? client.email ?? undefined}
        action={<Badge variant={meta.badge}>{meta.label}</Badge>}
      />

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-5xl flex-col gap-4 p-4 sm:p-6">
          <Link
            to="/clients"
            className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" /> All clients
          </Link>

          <SmartSummary
            entityId={client.id}
            getSummary={() => api.getClientSummary(client.id)}
            regenerateSummary={() => api.regenerateClientSummary(client.id)}
          />

          <div className="grid gap-4 lg:grid-cols-2">
            <ClientInfoCard client={client} onPatch={onPatch} busy={busy} />
            <CareCard client={client} regions={facets.regions} onPatch={onPatch} busy={busy} />
            <HoursCard hours={client.hours_this_week} />
            <ContactsCard
              clientId={client.id}
              contacts={client.contacts}
              onChanged={refreshAfterContacts}
            />
            <CaregiversCard caregivers={client.caregivers} />
            <ClientVisitsCard clientId={client.id} refreshKey={timelineKey} />
            <ClientDocumentsCard clientId={client.id} />
          </div>

          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Timeline
            </p>
            <div className="rounded-lg border">
              <EntityTimeline
                entityType="client"
                entityId={client.id}
                refreshKey={timelineKey}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
