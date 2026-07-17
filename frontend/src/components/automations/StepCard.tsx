import type { ComponentType } from "react";
import {
  ChevronDown,
  ChevronUp,
  Clock,
  GitBranch,
  ShieldAlert,
  Sigma,
  Sparkles,
  Trash2,
  Wrench,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  delayUnit,
  describeStep,
  isGatedTool,
  toolLabel,
  type Step,
} from "@/lib/recipe";
import type { Vocabulary } from "@/lib/api";
import { SchemaForm } from "./SchemaForm";
import { TemplateInsert } from "./TemplateInsert";
import { ConditionChips } from "./ConditionChips";

const STEP_ICONS: Record<Step["type"], ComponentType<{ className?: string }>> = {
  tool: Wrench,
  delay: Clock,
  condition: GitBranch,
  function: Sigma,
  generate: Sparkles,
};

const selectClass =
  "h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export interface StepEdit {
  onChange: (step: Step) => void;
  onRemove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  isFirst: boolean;
  isLast: boolean;
  vocabulary: Vocabulary;
  contextKeys: string[]; // save_as keys from earlier steps
}

// One THEN step. Read-mode shows a plain title + detail + gated chip; pass `edit`
// to render the per-type form (same component tree, not a fork). `gatedTools`
// overrides the fallback gated set once the vocabulary is loaded.
export function StepCard({
  step,
  index,
  gatedTools,
  edit,
}: {
  step: Step;
  index: number;
  gatedTools?: Set<string>;
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
            {step.type === "tool" ? toolLabel(step.tool) : describeStep(step)}
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
          <ReadDetail step={step} />
        )}
      </div>
    </div>
  );
}

function ReadDetail({ step }: { step: Step }) {
  const detail = readDetail(step);
  if (!detail) return null;
  return <p className="mt-1 break-words text-xs text-muted-foreground">{detail}</p>;
}

function readDetail(step: Step): string | null {
  switch (step.type) {
    case "tool": {
      const entries = Object.entries(step.input ?? {});
      return entries.length ? entries.map(([k, v]) => `${k}: ${String(v)}`).join("  ·  ") : null;
    }
    case "generate": {
      const bits = [
        step.prompt ? `“${truncate(step.prompt, 90)}”` : null,
        step.save_as ? `saved as ${step.save_as}` : null,
        step.model === "fast" ? "fast model" : null,
      ].filter(Boolean);
      return bits.join("  ·  ") || null;
    }
    case "function":
      return step.save_as ? `saved as ${step.save_as}` : null;
    case "condition":
      return "If false, the automation stops here.";
    default:
      return null;
  }
}

// --- editors ---------------------------------------------------------------
function StepEditor({ step, edit }: { step: Step; edit: StepEdit }) {
  const { onChange, vocabulary, contextKeys } = edit;

  if (step.type === "tool") {
    const tool = vocabulary.tools.find((t) => t.name === step.tool);
    return (
      <div className="space-y-2.5">
        <select
          className={selectClass}
          value={step.tool ?? ""}
          onChange={(e) => onChange({ ...step, tool: e.target.value, input: {} })}
        >
          <option value="">select an action…</option>
          <optgroup label="Safe">
            {vocabulary.tools.filter((t) => t.safe).map((t) => (
              <option key={t.name} value={t.name}>{t.label}</option>
            ))}
          </optgroup>
          <optgroup label="Requires approval">
            {vocabulary.tools.filter((t) => !t.safe).map((t) => (
              <option key={t.name} value={t.name}>{t.label}</option>
            ))}
          </optgroup>
        </select>
        {tool && (
          <SchemaForm
            schema={tool.input_schema}
            value={(step.input as Record<string, unknown>) ?? {}}
            onChange={(input) => onChange({ ...step, input })}
            contextKeys={contextKeys}
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
        <select
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          value={unit}
          onChange={(e) =>
            setDelay(e.target.value as "minutes" | "hours" | "days", amount || 1)
          }
        >
          <option value="minutes">minutes</option>
          <option value="hours">hours</option>
          <option value="days">days</option>
        </select>
      </div>
    );
  }

  if (step.type === "condition") {
    return (
      <div className="space-y-2">
        <ConditionChips
          conditions={step.conditions ?? []}
          onChange={(conditions) => onChange({ ...step, conditions, on_false: "stop" })}
          vocabulary={vocabulary}
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
        <select
          className={selectClass}
          value={step.function ?? ""}
          onChange={(e) => onChange({ ...step, function: e.target.value, args: {} })}
        >
          <option value="">select a computation…</option>
          {vocabulary.functions.map((f) => (
            <option key={f.name} value={f.name}>{f.name} — {f.description}</option>
          ))}
        </select>
        {fn && (
          <SchemaForm
            schema={fn.input_schema}
            value={(step.args as Record<string, unknown>) ?? {}}
            onChange={(args) => onChange({ ...step, args })}
            contextKeys={contextKeys}
          />
        )}
        <SaveAsField value={step.save_as} onChange={(v) => onChange({ ...step, save_as: v })} />
      </div>
    );
  }

  if (step.type === "generate") {
    return (
      <div className="space-y-2.5">
        <div>
          <div className="mb-1 flex items-center justify-between">
            <label className="text-xs font-medium text-muted-foreground">Prompt</label>
            <TemplateInsert
              contextKeys={contextKeys}
              onInsert={(t) => onChange({ ...step, prompt: `${step.prompt ?? ""}${t}` })}
            />
          </div>
          <Textarea
            value={step.prompt ?? ""}
            onChange={(e) => onChange({ ...step, prompt: e.target.value })}
            placeholder="Write a friendly welcome to {{entity.name}}…"
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
            <select
              className="h-9 rounded-md border border-input bg-background px-2 text-sm"
              value={step.model ?? "default"}
              onChange={(e) => onChange({ ...step, model: e.target.value as "default" | "fast" })}
            >
              <option value="default">default (higher quality)</option>
              <option value="fast">fast (cheaper)</option>
            </select>
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

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n)}…` : s;
}
