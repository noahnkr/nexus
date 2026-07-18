// Leads view — vertical content seam (Module 9), the frontend mirror of
// backend/app/services/views/leads.py. Stage labels/order/tones live here, not in
// core UI, so the leads pipeline stays re-templatable (M10 adds lib/caregivers.ts
// alongside). Stages are leads.status values — no separate stage model.
import type { LeadStatus } from "@/lib/api";
import {
  registerPipelineView,
  type PipelineViewConfig,
  type TriggerConvention,
} from "@/lib/pipeline";

// Badge variants this config uses (subset of the ui/badge variants).
type Tone = "default" | "secondary" | "info" | "success";

export interface LeadStage {
  key: LeadStatus;
  label: string;
  tone: Tone;
  terminal: boolean; // a stage a lead ends at (converted = won, lost = dropped)
}

// Ordered funnel: the four worked stages then the terminal drop-off. Tones —
// converted = success, lost = muted (secondary), the worked stages = info.
export const LEAD_STAGES: LeadStage[] = [
  { key: "new", label: "New", tone: "info", terminal: false },
  { key: "contacted", label: "Contacted", tone: "info", terminal: false },
  { key: "qualified", label: "Qualified", tone: "info", terminal: false },
  { key: "converted", label: "Converted", tone: "success", terminal: true },
  { key: "lost", label: "Lost", tone: "secondary", terminal: true },
];

const BY_KEY: Record<string, LeadStage> = Object.fromEntries(
  LEAD_STAGES.map((s) => [s.key, s]),
);

export function stageLabel(status: string): string {
  return BY_KEY[status]?.label ?? status;
}

export function stageTone(status: string): Tone {
  return BY_KEY[status]?.tone ?? "default";
}

// --- Pipeline view instance (9b) ---------------------------------------------
// The leads instance of the generic PipelineViewConfig. This is the ONLY place the
// leads trigger convention, tool allowlist, and stage-sequence naming live — core
// (lib/pipeline.ts, lib/recipe.ts, the Center) reads it through the registry.

// Entity event names (mirror services/tools/entities.py + routers/leads.py).
const LEAD_CREATED = "lead.created";
const LEAD_STAGE_CHANGED = "lead.stage_changed";

// Trigger convention (D3): entering the funnel at "new" IS creation, so stage
// `new` triggers on lead.created; every other stage triggers on lead.stage_changed
// with a managed condition pinning payload.to to that stage.
function buildLeadTrigger(stage: string): TriggerConvention {
  if (stage === "new") {
    return { trigger: { type: "event", event_type: LEAD_CREATED } };
  }
  return {
    trigger: { type: "event", event_type: LEAD_STAGE_CHANGED },
    managedCondition: { field: "trigger.payload.to", op: "eq", value: stage },
  };
}

export const LEADS_VIEW: PipelineViewConfig = {
  view: "leads",
  label: "Leads",
  entityType: "lead",
  stages: LEAD_STAGES.map((s) => ({ key: s.key, label: s.label, tone: s.tone })),
  // The four worked stages carry a sequence chip; `lost` is a drop-off, not worked.
  sequenceStages: ["new", "contacted", "qualified", "converted"],
  toolAllowlist: ["send_sms", "send_email", "create_task"],
  buildTrigger: buildLeadTrigger,
  sequenceRoute: (stage) => `/leads/stages/${stage}/sequence`,
  directoryRoute: "/leads",
  defaultName: (stage) => `Leads · ${stageLabel(stage)} sequence`,
};

registerPipelineView(LEADS_VIEW);
