import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ArrowLeft, PartyPopper, Users } from "lucide-react";
import { api, type Applicant, type ApplicantFacets, type ApplicantPatch } from "@/lib/api";
import { parseApiError } from "@/lib/utils";
import { stageLabel, stageTone } from "@/lib/caregivers";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { SmartSummary } from "@/components/leads/SmartSummary";
import { ApplicantInfoCard } from "@/components/caregivers/ApplicantInfoCard";
import { EntityTimeline } from "@/components/events/EntityTimeline";

export function CaregiverProfilePage() {
  const { id = "" } = useParams();
  const [applicant, setApplicant] = useState<Applicant | null>(null);
  const [facets, setFacets] = useState<ApplicantFacets>({
    sources: [],
    regions: [],
    qualifications: [],
  });
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [busy, setBusy] = useState(false);
  const [timelineKey, setTimelineKey] = useState(0);
  // Set from a hire PATCH response (promoted_resource_name) — the success banner.
  const [hiredName, setHiredName] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setApplicant(await api.getApplicant(id));
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
    api.getApplicantFacets().then(setFacets).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [load]);

  // A write (field edit or stage move) PATCHes only the changed fields, then
  // refreshes the applicant and bumps the timeline so the new event shows at once.
  const onPatch = async (patch: ApplicantPatch) => {
    setBusy(true);
    try {
      const updated = await api.patchApplicant(id, patch);
      setApplicant(updated);
      setTimelineKey((k) => k + 1);
      if (updated.promoted_resource_name) {
        setHiredName(updated.promoted_resource_name);
        toast.success(`Hired — caregiver record created for ${updated.promoted_resource_name}`);
      } else if (patch.stage) {
        toast.success(`Moved to ${stageLabel(patch.stage)}`);
      }
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
        <PageHeader title="Applicant" />
        <div className="flex flex-col gap-4 p-6">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      </div>
    );
  }

  if (notFound || !applicant) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <PageHeader title="Applicant" />
        <div className="p-6">
          <EmptyState
            icon={Users}
            title="Applicant not found"
            description="This applicant doesn't exist or isn't visible to you."
            action={
              <Link to="/caregivers" className="text-sm text-primary hover:underline">
                Back to caregivers
              </Link>
            }
          />
        </div>
      </div>
    );
  }

  const isHired = applicant.stage === "hired";

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title={applicant.name}
        description={applicant.email ?? applicant.phone ?? undefined}
        action={
          <Badge variant={stageTone(applicant.stage)}>{stageLabel(applicant.stage)}</Badge>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-4 p-6">
          <Link
            to="/caregivers"
            className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" /> All caregivers
          </Link>

          {isHired && (
            <div className="flex items-center gap-2 rounded-lg border border-success/30 bg-success/5 p-3 text-sm text-foreground">
              <PartyPopper className="h-4 w-4 shrink-0 text-success" />
              <span>
                {hiredName
                  ? `Hired — a caregiver record was created for ${hiredName}.`
                  : "This applicant has been hired and added to the caregiver roster."}
              </span>
            </div>
          )}

          <SmartSummary
            entityId={applicant.id}
            getSummary={() => api.getApplicantSummary(applicant.id)}
            regenerateSummary={() => api.regenerateApplicantSummary(applicant.id)}
          />

          <ApplicantInfoCard
            applicant={applicant}
            facets={facets}
            onPatch={onPatch}
            busy={busy}
          />

          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Timeline
            </p>
            <div className="rounded-lg border">
              <EntityTimeline
                entityType="applicant"
                entityId={applicant.id}
                refreshKey={timelineKey}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
