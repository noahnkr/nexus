// Caregivers view — vertical content seam (Module 10), the frontend mirror of
// backend/app/services/views/caregivers.py. Stage labels/order/tones live here, not
// in core UI, so the hiring pipeline stays re-templatable (it sits alongside
// lib/leads.ts). Stages are applicants.stage values — no separate stage model.
//
import type { ApplicantStage } from "@/lib/api";
import {
  registerPipelineView,
  type PipelineViewConfig,
  type TriggerConvention,
} from "@/lib/pipeline";

// Badge variants this config uses (subset of the ui/badge variants).
type Tone = "default" | "secondary" | "info" | "success";

export interface CaregiverStage {
  key: ApplicantStage;
  label: string;
  tone: Tone;
  terminal: boolean; // a stage an applicant ends at (hired = won, rejected = dropped)
}

// Ordered hiring funnel: five worked stages then the terminal drop-off. Tones —
// hired = success, rejected = muted (secondary), the worked stages = info.
export const CAREGIVER_STAGES: CaregiverStage[] = [
  { key: "applied", label: "Applied", tone: "info", terminal: false },
  { key: "screening", label: "Screening", tone: "info", terminal: false },
  { key: "interview", label: "Interview", tone: "info", terminal: false },
  { key: "offer", label: "Offer", tone: "info", terminal: false },
  { key: "hired", label: "Hired", tone: "success", terminal: true },
  { key: "rejected", label: "Rejected", tone: "secondary", terminal: true },
];

const BY_KEY: Record<string, CaregiverStage> = Object.fromEntries(
  CAREGIVER_STAGES.map((s) => [s.key, s]),
);

export function stageLabel(stage: string): string {
  return BY_KEY[stage]?.label ?? stage;
}

export function stageTone(stage: string): Tone {
  return BY_KEY[stage]?.tone ?? "default";
}

// --- Pipeline view instance (10b) --------------------------------------------
// The caregivers instance of the generic PipelineViewConfig. The ONLY place the
// caregivers trigger convention, tool allowlist, and stage-sequence naming live —
// core (lib/pipeline.ts, lib/recipe.ts, the Center) reads it through the registry,
// so registering here (App.tsx statically imports this module) makes the funnel
// strip, the shared stage-sequence builder, and the Center's binding awareness all
// work with zero vertical branches in core UI.

// Entity event names (mirror services/tools/entities.py + routers/applicants.py).
const APPLICANT_CREATED = "applicant.created";
const APPLICANT_STAGE_CHANGED = "applicant.stage_changed";

// Trigger convention (mirrors leads): entering the funnel at "applied" IS creation,
// so stage `applied` triggers on applicant.created; every other stage triggers on
// applicant.stage_changed with a managed condition pinning payload.to to that stage.
function buildApplicantTrigger(stage: string): TriggerConvention {
  if (stage === "applied") {
    return { trigger: { type: "event", event_type: APPLICANT_CREATED } };
  }
  return {
    trigger: { type: "event", event_type: APPLICANT_STAGE_CHANGED },
    managedCondition: { field: "trigger.payload.to", op: "eq", value: stage },
  };
}

export const CAREGIVERS_VIEW: PipelineViewConfig = {
  view: "caregivers",
  label: "Caregivers",
  entityType: "applicant",
  stages: CAREGIVER_STAGES.map((s) => ({ key: s.key, label: s.label, tone: s.tone })),
  // Every stage carries a sequence chip — including `rejected` (the PRD's automated
  // denied email), the deliberate divergence from leads' chip-less `lost`. It's
  // config data, not component logic.
  sequenceStages: CAREGIVER_STAGES.map((s) => s.key),
  toolAllowlist: ["send_email", "send_sms", "create_task"],
  buildTrigger: buildApplicantTrigger,
  sequenceRoute: (stage) => `/caregivers/stages/${stage}/sequence`,
  directoryRoute: "/caregivers",
  defaultName: (stage) => `Caregivers · ${stageLabel(stage)} sequence`,
};

registerPipelineView(CAREGIVERS_VIEW);
