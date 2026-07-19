import type { ComponentType } from "react";
import {
  ChevronDown,
  ChevronUp,
  Clock,
  GitBranch,
  Hourglass,
  ShieldAlert,
  Sigma,
  Sparkles,
  Trash2,
  Wrench,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Select, type SelectOption } from "@/components/ui/Select";
import {
  delayUnit,
  describeStep,
  eventTypeLabel,
  functionLabel,
  isDisplayableEventType,
  isGatedTool,
  toolLabel,
  type Step,
} from "@/lib/recipe";
import type { FieldCatalog, Vocabulary } from "@/lib/api";
import { labelizeTemplate } from "@/lib/template";
import { SchemaForm } from "./SchemaForm";
import { FormulaEditor } from "./FormulaEditor";
import { TokenField, type FieldContext } from "./FieldPicker";
import { ConditionChips } from "./ConditionChips";

const STEP_ICONS: Record<Step["type"], ComponentType<{ className?: string }>> = {
  tool: Wrench,
  delay: Clock,
  condition: GitBranch,
  function: Sigma,
  generate: Sparkles,
  wait_until: Hourglass,
};

type DurationUnit = "minutes" | "hours" | "days";

const DURATION_UNITS: SelectOption<DurationUnit>[] = [
  { value: "minutes", label: "minutes" },
  { value: "hours", label: "hours" },
  { value: "days", label: "days" },
];

const UNIT_TO_MIN: Record<DurationUnit, number> = { minutes: 1, hours: 60, days: 1440 };

// Split a stored minutes count into an [amount, unit] pair for the wait-until
// timeout editor — pick the largest unit it divides evenly into. null → indefinite.
function timeoutParts(min: number | null | undefined): { amount: number | ""; unit: DurationUnit } {
  if (min == null) return { amount: "", unit: "minutes" };
  if (min % 1440 === 0) return { amount: min / 1440, unit: "days" };
  if (min % 60 === 0) return { amount: min / 60, unit: "hours" };
  return { amount: min, unit: "minutes" };
}

export interface StepEdit {
  onChange: (step: Step) => void;
  onRemove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  isFirst: boolean;
  isLast: boolean;
  vocabulary: Vocabulary;
  ctx: FieldContext; // vocabulary + selected trigger + this step's prior save_as keys
}

// One THEN step. Read-mode shows a plain title + detail + gated chip; pass `edit`
// to render the per-type form (same component tree, not a fork). `gatedTools`
// overrides the fallback gated set once the vocabulary is loaded.
export function StepCard({
  step,
  index,
  gatedTools,
  catalog,
  edit,
}: {
  step: Step;
  index: number;
  gatedTools?: Set<string>;
  catalog?: FieldCatalog; // read-mode: renders {{paths}} as plain-language labels
  edit?: StepEdit;
}) {
  const Icon = STEP_ICONS[step.type] ?? Wrench;
  const gated = step.type === "tool" && isGatedTool(step.tool, gatedTools);

  return (
    <div className="flex gap-3 rounded-lg border bg-card p-3 shadow-sm">
      <div className="flex flex-col items-center gap-1 pt-0.5">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-muted text-[11px] font-semibold tabular-nums text-muted-foreground">
          {index + 1}
        </span>
        {edit && (
          <div className="flex flex-col">
            <button
              type="button"
              disabled={edit.isFirst}
              onClick={edit.onMoveUp}
              className="text-muted-foreground hover:text-foreground disabled:opacity-30"
              aria-label="Move up"
            >
              <ChevronUp className="h-4 w-4" />
            </button>
            <button
              type="button"
              disabled={edit.isLast}
              onClick={edit.onMoveDown}
              className="text-muted-foreground hover:text-foreground disabled:opacity-30"
              aria-label="Move down"
            >
              <ChevronDown className="h-4 w-4" />
            </button>
          </div>
        )}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4 shrink-0 text-primary" />
          <span className="text-sm font-medium">
            {step.type === "tool" ? toolLabel(step.tool) : describeStep(step, catalog)}
          </span>
          {gated && (
            <Badge variant="warning" className="gap-1">
              <ShieldAlert className="h-3 w-3" /> Requires approval
            </Badge>
          )}
          {edit && (
            <button
              type="button"
              onClick={edit.onRemove}
              className="ml-auto text-muted-foreground hover:text-destructive"
              aria-label="Remove step"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          )}
        </div>

        {edit ? (
          <div className="mt-3">
            <StepEditor step={step} edit={edit} />
          </div>
        ) : (
          <ReadDetail step={step} catalog={catalog} />
        )}
      </div>
    </div>
  );
}

function ReadDetail({ step, catalog }: { step: Step; catalog?: FieldCatalog }) {
  const detail = readDetail(step, catalog);
  if (!detail) return null;
  return <p className="mt-1 break-words text-xs text-muted-foreground">{detail}</p>;
}

function readDetail(step: Step, catalog?: FieldCatalog): string | null {
  const lbl = (s: string) => labelizeTemplate(s, catalog);
  switch (step.type) {
    case "tool": {
      const entries = Object.entries(step.input ?? {});
      return entries.length
        ? entries.map(([k, v]) => `${k}: ${lbl(String(v))}`).join("  ·  ")
        : null;
    }
    case "generate": {
      const bits = [
        step.prompt ? `“${truncate(lbl(step.prompt), 90)}”` : null,
        step.save_as ? `saved as ${step.save_as}` : null,
        step.model === "fast" ? "fast model" : null,
      ].filter(Boolean);
      return bits.join("  ·  ") || null;
    }
    case "function": {
      // A formula reads as its expression with field chips spelled out, e.g.
      // "(Hourly rate + 2) * 1.5 · saved as score" — not just the save_as name.
      const formula = (step.args as Record<string, unknown> | undefined)?.formula;
      const bits = [
        typeof formula === "string" && formula.trim() ? lbl(formula) : null,
        step.save_as ? `saved as ${step.save_as}` : null,
      ].filter(Boolean);
      return bits.join("  ·  ") || null;
    }
    case "condition":
      return "If false, the automation stops here.";
    case "wait_until": {
      const bits = [
        step.event_type ? `event: ${step.event_type}` : null,
        step.timeout_minutes ? `times out after ${step.timeout_minutes} min` : "no timeout",
      ].filter(Boolean);
      return bits.join("  ·  ") || null;
    }
    default:
      return null;
  }
}

// --- editors ---------------------------------------------------------------
function StepEditor({ step, edit }: { step: Step; edit: StepEdit }) {
  const { onChange, vocabulary, ctx } = edit;

  if (step.type === "tool") {
    const tool = vocabulary.tools.find((t) => t.name === step.tool);
    const safeOptions: SelectOption[] = vocabulary.tools
      .filter((t) => t.safe)
      .map((t) => ({ value: t.name, label: t.label }));
    const gatedOptions: SelectOption[] = vocabulary.tools
      .filter((t) => !t.safe)
      .map((t) => ({ value: t.name, label: t.label }));
    return (
      <div className="space-y-2.5">
        <Select
          value={step.tool ?? ""}
          onChange={(v) => onChange({ ...step, tool: v, input: {} })}
          groups={[
            { label: "Safe", options: safeOptions },
            { label: "Requires approval", icon: ShieldAlert, options: gatedOptions },
          ]}
          placeholder="select an action…"
          aria-label="Action"
        />
        {tool && (
          <SchemaForm
            schema={tool.input_schema}
            value={(step.input as Record<string, unknown>) ?? {}}
            onChange={(input) => onChange({ ...step, input })}
            ctx={ctx}
          />
        )}
        <SaveAsField
          value={step.save_as}
          onChange={(v) => onChange({ ...step, save_as: v })}
        />
      </div>
    );
  }

  if (step.type === "delay") {
    const [unit, amount] = delayUnit(step);
    const setDelay = (u: "minutes" | "hours" | "days", n: number) =>
      onChange({ type: "delay", minutes: undefined, hours: undefined, days: undefined, [u]: n });
    return (
      <div className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">Wait</span>
        <Input
          type="number"
          min={1}
          value={amount || 1}
          onChange={(e) => setDelay(unit, Math.max(1, Number(e.target.value) || 1))}
          className="w-24"
        />
        <Select<DurationUnit>
          className="w-32"
          value={unit}
          onChange={(v) => setDelay(v, amount || 1)}
          options={DURATION_UNITS}
          aria-label="Delay unit"
        />
      </div>
    );
  }

  if (step.type === "condition") {
    return (
      <div className="space-y-2">
        <ConditionChips
          conditions={step.conditions ?? []}
          onChange={(conditions) => onChange({ ...step, conditions, on_false: "stop" })}
          ctx={ctx}
          label="Only if"
          addLabel="Add check"
        />
        <p className="text-xs text-muted-foreground">
          If these aren't all true, the automation stops here.
        </p>
      </div>
    );
  }

  if (step.type === "function") {
    const fn = vocabulary.functions.find((f) => f.name === step.function);
    return (
      <div className="space-y-2.5">
        <Select
          value={step.function ?? ""}
          onChange={(v) => onChange({ ...step, function: v, args: {} })}
          options={vocabulary.functions.map((f) => ({
            value: f.name,
            label: functionLabel(f.name),
          }))}
          placeholder="select a computation…"
          aria-label="Computation"
        />
        {/* `formula` gets a purpose-built editor; every other function (including
            legacy weighted_score, whose free-form object args fall back to raw
            JSON) keeps the generic SchemaForm. */}
        {fn && step.function === "formula" && (
          <FormulaEditor
            value={String((step.args as Record<string, unknown>)?.formula ?? "")}
            onChange={(formula) => onChange({ ...step, args: { formula } })}
            ctx={ctx}
          />
        )}
        {fn && step.function !== "formula" && (
          <SchemaForm
            schema={fn.input_schema}
            value={(step.args as Record<string, unknown>) ?? {}}
            onChange={(args) => onChange({ ...step, args })}
            ctx={ctx}
          />
        )}
        <SaveAsField value={step.save_as} onChange={(v) => onChange({ ...step, save_as: v })} />
      </div>
    );
  }

  if (step.type === "wait_until") {
    return (
      <div className="space-y-2.5">
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Wait until this event happens
          </label>
          <Select
            value={step.event_type ?? ""}
            onChange={(v) => onChange({ ...step, event_type: v })}
            options={vocabulary.triggers.event_types
              .filter(isDisplayableEventType)
              .map((et) => ({ value: et, label: eventTypeLabel(et), mono: et }))}
            placeholder="select event…"
            searchable
            aria-label="Wait-until event"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Matching these conditions (optional)
          </label>
          <ConditionChips
            conditions={step.conditions ?? []}
            onChange={(conditions) => onChange({ ...step, conditions })}
            ctx={ctx}
            label="Where"
            addLabel="Add condition"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Give up after — leave blank to wait indefinitely
          </label>
          <TimeoutField
            minutes={step.timeout_minutes}
            onChange={(timeout_minutes) => onChange({ ...step, timeout_minutes })}
          />
        </div>
      </div>
    );
  }

  if (step.type === "generate") {
    return (
      <div className="space-y-2.5">
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Prompt</label>
          <TokenField
            value={step.prompt ?? ""}
            onChange={(prompt) => onChange({ ...step, prompt })}
            ctx={ctx}
            multiline
            placeholder="Write a friendly welcome…"
          />
        </div>
        <div className="flex gap-3">
          <div className="flex-1">
            <SaveAsField
              value={step.save_as}
              onChange={(v) => onChange({ ...step, save_as: v })}
              required
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Model</label>
            <Select<"default" | "fast">
              className="w-52"
              value={step.model ?? "default"}
              onChange={(v) => onChange({ ...step, model: v })}
              options={[
                { value: "default", label: "default (higher quality)" },
                { value: "fast", label: "fast (cheaper)" },
              ]}
              aria-label="Model"
            />
          </div>
        </div>
      </div>
    );
  }

  return null;
}

function SaveAsField({
  value,
  onChange,
  required,
}: {
  value: string | undefined;
  onChange: (v: string | undefined) => void;
  required?: boolean;
}) {
  return (
    <div>
      <label className="mb-1 flex items-center gap-1 text-xs font-medium text-muted-foreground">
        Save result as {required && <span className="text-destructive">*</span>}
        <span className="font-normal text-muted-foreground/70">
          — later steps reference it as {"{{context.<name>}}"}
        </span>
      </label>
      <Input
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value.trim() || undefined)}
        placeholder="e.g. message"
      />
    </div>
  );
}

// The wait-until timeout, authored like the delay step: an integer + a unit,
// stored as minutes. Blank amount = wait indefinitely (null).
function TimeoutField({
  minutes,
  onChange,
}: {
  minutes: number | null | undefined;
  onChange: (minutes: number | null) => void;
}) {
  const { amount, unit } = timeoutParts(minutes);
  const emit = (nextAmount: number | "", nextUnit: DurationUnit) =>
    onChange(nextAmount === "" ? null : nextAmount * UNIT_TO_MIN[nextUnit]);
  return (
    <div className="flex items-center gap-2">
      <Input
        type="number"
        min={1}
        value={amount === "" ? "" : String(amount)}
        onChange={(e) => {
          const raw = e.target.value;
          emit(raw === "" ? "" : Math.max(1, Number(raw) || 1), unit);
        }}
        placeholder="no timeout"
        className="w-24"
      />
      <Select<DurationUnit>
        className="w-32"
        value={unit}
        onChange={(v) => emit(amount, v)}
        options={DURATION_UNITS}
        aria-label="Timeout unit"
      />
    </div>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n)}…` : s;
}
