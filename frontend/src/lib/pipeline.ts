// Pipeline view config (Module 9b) — the generic contract a dashboard view (Leads
// now, Caregivers in M10) supplies so the FunnelStrip, the stage-sequence builder,
// and the Automations Center's binding awareness all work without vertical
// branches in core UI. lib/leads.ts fills in the leads instance and registers it;
// lib/recipe.ts and the Center components stay vertical-free by reading this map.
import type { Condition, Trigger } from "@/lib/recipe";

export type StageTone = "default" | "secondary" | "info" | "success";

export interface PipelineStage {
  key: string;
  label: string;
  tone: StageTone;
}

// The trigger a stage's sequence fires on, built by the config (never hand-typed by
// the user). `managedCondition` is the fixed first IF condition the constrained
// builder renders as prose, not as an editable chip.
export interface TriggerConvention {
  trigger: Trigger;
  managedCondition?: Condition;
}

export interface PipelineViewConfig {
  view: string; // the binding.view value, e.g. "leads"
  label: string; // "Leads"
  entityType: string; // "lead" — the entity-event entity_type
  stages: PipelineStage[]; // ordered, including terminal stages
  sequenceStages: string[]; // which stages carry a sequence chip
  toolAllowlist: string[]; // tools the constrained builder offers
  buildTrigger: (stage: string) => TriggerConvention;
  sequenceRoute: (stage: string) => string;
  directoryRoute: string;
  defaultName: (stage: string) => string;
}

const REGISTRY: Record<string, PipelineViewConfig> = {};

export function registerPipelineView(config: PipelineViewConfig): void {
  REGISTRY[config.view] = config;
}

export function getPipelineView(view: string | undefined | null): PipelineViewConfig | undefined {
  return view ? REGISTRY[view] : undefined;
}

function bindingView(binding: Record<string, unknown> | null | undefined): string | null {
  if (!binding || typeof binding !== "object") return null;
  const v = binding.view;
  return typeof v === "string" && v ? v : null;
}

function bindingStage(binding: Record<string, unknown> | null | undefined): string | null {
  if (!binding || typeof binding !== "object") return null;
  const s = binding.stage;
  return typeof s === "string" && s ? s : null;
}

// "Leads · Contacted" for the Center's binding chip. Falls back to the raw view
// name for an unregistered view, or null when there's no binding.
export function describeBinding(
  binding: Record<string, unknown> | null | undefined,
): string | null {
  const view = bindingView(binding);
  if (!view) return null;
  const config = REGISTRY[view];
  if (!config) return view;
  const stage = bindingStage(binding);
  if (!stage) return config.label;
  const label = config.stages.find((s) => s.key === stage)?.label ?? stage;
  return `${config.label} · ${label}`;
}

// Where a bound automation's Edit affordance should route: its view's stage
// builder. Null when the view isn't registered (fall back to the generic builder).
export function bindingEditRoute(
  binding: Record<string, unknown> | null | undefined,
): string | null {
  const view = bindingView(binding);
  const stage = bindingStage(binding);
  const config = view ? REGISTRY[view] : undefined;
  if (!config || !stage) return null;
  return config.sequenceRoute(stage);
}
