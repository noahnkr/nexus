// Recipe vocabulary mirrored from the M7 engine (services/automations/recipe.py),
// plus `describeRecipe()` — the shared plain-language renderer used by the grid
// cards, the detail page's read-mode components, and 8b's editable sentence line.
// Non-technical staff read these strings; raw recipe JSON stays behind a toggle.
import { labelForPath, labelizeTemplate } from "./template";
import type { FieldCatalog } from "./api";

export type TriggerType = "event" | "cron" | "manual";

export interface Trigger {
  type: TriggerType;
  event_type?: string;
  source_system?: string | null;
  expression?: string;
}

export type Operator =
  | "eq" | "neq" | "gt" | "gte" | "lt" | "lte"
  | "contains" | "not_contains" | "exists" | "not_exists";

export const OPERATORS: Operator[] = [
  "eq", "neq", "gt", "gte", "lt", "lte",
  "contains", "not_contains", "exists", "not_exists",
];

export interface Condition {
  field: string;
  op: Operator | string;
  value?: unknown;
}

export type StepType =
  | "tool"
  | "delay"
  | "condition"
  | "function"
  | "generate"
  | "wait_until";

export interface Step {
  type: StepType;
  // tool
  tool?: string;
  input?: Record<string, unknown>;
  save_as?: string;
  // delay
  minutes?: number;
  hours?: number;
  days?: number;
  // condition
  conditions?: Condition[];
  on_false?: string;
  // function
  function?: string;
  args?: Record<string, unknown>;
  // generate
  prompt?: string;
  model?: "default" | "fast";
  // wait_until
  event_type?: string;
  timeout_minutes?: number | null;
}

export interface Recipe {
  trigger: Trigger;
  conditions: Condition[];
  steps: Step[];
}

// Plain-language tool labels (noun form) — never surface raw tool names to staff.
// The vocabulary endpoint (8b) is authoritative; this is the fallback map so 8a's
// read-mode surfaces render before the builder exists.
export const TOOL_LABELS: Record<string, string> = {
  update_lead_status: "Update lead status",
  update_client_status: "Update client status",
  create_schedule: "Schedule a visit",
  cancel_schedule: "Cancel a visit",
  send_sms: "Send a text message",
  send_email: "Send an email",
  create_task: "Create a task",
};

export function toolLabel(name: string | undefined): string {
  if (!name) return "Run a tool";
  return TOOL_LABELS[name] ?? name.replace(/_/g, " ");
}

// Gated (approval-required) tools. The 8b vocabulary endpoint is authoritative;
// this fallback set lets 8a's read surfaces show the amber chip before the builder
// exists. Callers may pass an explicit set (from vocabulary) to override.
export const KNOWN_GATED_TOOLS = new Set([
  "update_lead_status", "update_client_status", "create_schedule",
  "cancel_schedule", "send_sms", "send_email",
]);

export function isGatedTool(name: string | undefined, gatedTools?: Set<string>): boolean {
  if (!name) return false;
  return (gatedTools ?? KNOWN_GATED_TOOLS).has(name);
}

const SOURCE_LABELS: Record<string, string> = {
  welcomehome: "WelcomeHome",
  goto: "GoTo Connect",
  wellsky: "WellSky",
  gmail: "Gmail",
  gcal: "Google Calendar",
};

export function sourceLabel(source: string | null | undefined): string {
  if (!source) return "";
  return SOURCE_LABELS[source] ?? source.charAt(0).toUpperCase() + source.slice(1);
}

// "lead.created" -> "a lead is created"; degrades gracefully for unknown shapes.
const VERB_PHRASES: Record<string, string> = {
  created: "is created",
  updated: "is updated",
  deleted: "is deleted",
  received: "is received",
  completed: "is completed",
  cancelled: "is cancelled",
  matched: "is matched",
};

export function humanizeEventType(eventType: string | undefined): string {
  if (!eventType) return "an event occurs";
  const [entity, verb] = eventType.split(".");
  const noun = (entity ?? eventType).replace(/_/g, " ");
  const phrase = verb ? VERB_PHRASES[verb] ?? `is ${verb.replace(/_/g, " ")}` : "occurs";
  return `a ${noun} ${phrase}`;
}

// Plain-language event-type labels for the trigger dropdown (Module 13). A curated
// map for the core-known types, with a humanized fallback for anything else
// (`applicant.stage_changed` → "Applicant stage changed", `visit.checked_in` →
// "Visit checked in"). Like M11's tokens, this is a *view* — the recipe JSON and the
// vocabulary endpoint still carry the raw `entity.verb` type, shown as mono
// secondary text beside the label.
export const EVENT_TYPE_LABELS: Record<string, string> = {
  "lead.created": "Lead created",
  "lead.updated": "Lead updated",
  "lead.stage_changed": "Lead stage changed",
  "applicant.created": "Applicant created",
  "applicant.updated": "Applicant updated",
  "applicant.stage_changed": "Applicant stage changed",
  "client.created": "Client created",
  "client.updated": "Client updated",
  "schedule.created": "Visit scheduled",
  "schedule.updated": "Visit updated",
  "schedule.cancelled": "Visit cancelled",
  "sms.received": "Text message received",
  "email.received": "Email received",
  "call.received": "Call received",
};

// Test/dummy noise that leaked into the observed event/source facets from test
// runs (the events table is the source of the vocabulary's observed types). These
// are never real triggers, so the builder and filters hide them — a display filter,
// not a semantic interpretation of vertical names.
const JUNK_PREFIXES = new Set([
  "test", "evtest", "vocab", "dummy", "example", "sample", "foo", "bar", "await", "other",
]);

// A leading segment (before the first ".") in the denylist, or a segment that is a
// bare junk token, hides the type/source. "await.a.b", "evtest.x", "test" → hidden.
export function isDisplayableEventType(eventType: string): boolean {
  const head = eventType.split(".")[0]?.toLowerCase() ?? "";
  return Boolean(head) && !JUNK_PREFIXES.has(head);
}

export function isDisplayableSource(source: string): boolean {
  return !JUNK_PREFIXES.has(source.split(".")[0]?.toLowerCase() ?? "");
}

export function eventTypeLabel(eventType: string | undefined): string {
  if (!eventType) return "an event";
  const curated = EVENT_TYPE_LABELS[eventType];
  if (curated) return curated;
  // Humanize: "applicant.stage_changed" -> "Applicant stage changed".
  const words = eventType.replace(/\./g, " ").replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : eventType;
}

// Plain-language operator labels for the condition builder's operator dropdown
// (Module 13). Mirrors OP_PHRASES (the read-mode symbols) but spelled out for the
// edit UI where the user is choosing, not scanning. Unknown ops fall back to raw.
export const OPERATOR_LABELS: Record<string, string> = {
  eq: "is",
  neq: "is not",
  gt: "is greater than",
  gte: "is greater than or equal to",
  lt: "is less than",
  lte: "is less than or equal to",
  contains: "contains",
  not_contains: "does not contain",
  exists: "has a value",
  not_exists: "is empty",
};

export function operatorLabel(op: string): string {
  return OPERATOR_LABELS[op] ?? op.replace(/_/g, " ");
}

export function describeTrigger(trigger: Trigger): string {
  if (trigger.type === "event") {
    const from = trigger.source_system ? ` from ${sourceLabel(trigger.source_system)}` : "";
    return `When ${humanizeEventType(trigger.event_type)}${from}`;
  }
  if (trigger.type === "cron") {
    return `On a schedule (${trigger.expression ?? "—"})`;
  }
  return "Run manually";
}

const OP_PHRASES: Record<string, string> = {
  eq: "is",
  neq: "is not",
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  contains: "contains",
  not_contains: "does not contain",
  exists: "is set",
  not_exists: "is not set",
};

// Strip the root prefix and prettify: "trigger.payload.status" -> "status".
export function humanizeField(field: string): string {
  const parts = field.split(".");
  const tail = parts[parts.length - 1] ?? field;
  return tail.replace(/_/g, " ");
}

// Read-mode condition text. With a catalog, the field and any templated value are
// rendered in plain language ("Lead — Status is set", 'is "Phone"') instead of raw
// dotted paths (Module 11b). Without one it falls back to the humanized tail.
export function describeCondition(c: Condition, catalog?: FieldCatalog): string {
  const field = catalog ? labelForPath(c.field, catalog) : humanizeField(c.field);
  const op = OP_PHRASES[c.op] ?? c.op;
  if (c.op === "exists" || c.op === "not_exists") return `${field} ${op}`;
  return `${field} ${op} ${formatValue(c.value, catalog)}`;
}

function formatValue(v: unknown, catalog?: FieldCatalog): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return `"${labelizeTemplate(v, catalog)}"`;
  return String(v);
}

// Plain-language function names for the builder select + read mode (Module 11b).
export const FUNCTION_LABELS: Record<string, string> = {
  formula: "Calculate a value",
  // Retired in M15c; kept only so a legacy recipe still renders a readable name
  // rather than a raw identifier.
  weighted_score: "Calculate a score (retired)",
  days_since: "Days since a date",
  days_until: "Days until a date",
  now: "Current date & time",
};

export function functionLabel(name: string | undefined): string {
  if (!name) return "a calculation";
  return FUNCTION_LABELS[name] ?? name.replace(/_/g, " ");
}

export function describeStep(step: Step, catalog?: FieldCatalog): string {
  switch (step.type) {
    case "tool":
      return toolLabel(step.tool);
    case "delay": {
      const [unit, n] = delayUnit(step);
      return `Wait ${n} ${n === 1 ? unit.slice(0, -1) : unit}`;
    }
    case "condition": {
      const list = (step.conditions ?? []).map((c) => describeCondition(c, catalog)).join(" and ");
      return list ? `Continue only if ${list}` : "Continue only if …";
    }
    case "function":
      return functionLabel(step.function);
    case "generate":
      return "Write a message with AI";
    case "wait_until":
      return step.event_type
        ? `Wait until ${humanizeEventType(step.event_type)}`
        : "Wait until an event happens";
    default:
      return "Step";
  }
}

// Returns [unit, amount] for whichever unit the delay uses.
export function delayUnit(step: Step): ["minutes" | "hours" | "days", number] {
  if (step.days != null) return ["days", step.days];
  if (step.hours != null) return ["hours", step.hours];
  return ["minutes", step.minutes ?? 0];
}

// Run status vocabulary (engine states) + display metadata, shared by the run
// list, timeline, and grid last-run line. Defined here (not api.ts) so recipe.ts
// stays the single source of engine vocabulary with no circular import.
export type RunStatus =
  | "running"
  | "waiting"
  | "waiting_approval"
  | "completed"
  | "failed"
  | "cancelled";

type BadgeTone = "success" | "warning" | "info" | "destructive" | "secondary";

export const RUN_STATUS_META: Record<RunStatus, { label: string; tone: BadgeTone }> = {
  running: { label: "Running", tone: "info" },
  waiting: { label: "Waiting", tone: "info" },
  waiting_approval: { label: "Awaiting approval", tone: "warning" },
  completed: { label: "Completed", tone: "success" },
  failed: { label: "Failed", tone: "destructive" },
  cancelled: { label: "Cancelled", tone: "secondary" },
};

export const ACTIVE_RUN_STATES: RunStatus[] = ["running", "waiting", "waiting_approval"];

export function isActiveRun(status: RunStatus): boolean {
  return ACTIVE_RUN_STATES.includes(status);
}

export interface RecipeSummary {
  trigger: string;
  stepCount: number;
}

export function describeRecipe(recipe: Recipe): RecipeSummary {
  return {
    trigger: describeTrigger(recipe.trigger),
    stepCount: recipe.steps?.length ?? 0,
  };
}
