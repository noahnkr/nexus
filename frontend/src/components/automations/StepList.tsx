import { useEffect, useRef, useState } from "react";
import {
  Clock,
  GitBranch,
  Hourglass,
  ListOrdered,
  Plus,
  Sigma,
  Sparkles,
  Wrench,
} from "lucide-react";
import type { ComponentType } from "react";
import { Button } from "@/components/ui/button";
import { type Step, type StepType, type Trigger } from "@/lib/recipe";
import type { Vocabulary } from "@/lib/api";
import { StepCard } from "./StepCard";

const NEW_STEP: Record<StepType, () => Step> = {
  tool: () => ({ type: "tool", tool: "", input: {} }),
  delay: () => ({ type: "delay", minutes: 5 }),
  condition: () => ({ type: "condition", conditions: [], on_false: "stop" }),
  // Pre-select `formula`: it's the one users want ("run a calculation"), and it
  // opens the FormulaEditor instead of the generic schema form. weighted_score
  // stays reachable from the function dropdown for legacy recipes.
  function: () => ({ type: "function", function: "formula", args: { formula: "" } }),
  generate: () => ({ type: "generate", prompt: "", save_as: "message", model: "default" }),
  wait_until: () => ({ type: "wait_until", event_type: "", conditions: [], timeout_minutes: null }),
};

const ADD_OPTIONS: { type: StepType; label: string; icon: ComponentType<{ className?: string }> }[] = [
  { type: "tool", label: "Run an action", icon: Wrench },
  { type: "generate", label: "Write with AI", icon: Sparkles },
  { type: "delay", label: "Wait", icon: Clock },
  { type: "wait_until", label: "Wait until an event…", icon: Hourglass },
  { type: "condition", label: "Only continue if…", icon: GitBranch },
  { type: "function", label: "Run a calculation", icon: Sigma },
];

// The THEN list: reorderable step cards (up/down, not dnd) plus an add-step menu.
// `contextKeys` for each card is the set of `save_as` names produced by earlier
// steps, so the template inserter only offers values that actually exist yet.
export function StepList({
  steps,
  onChange,
  vocabulary,
  trigger,
}: {
  steps: Step[];
  onChange: (steps: Step[]) => void;
  vocabulary: Vocabulary;
  trigger: Trigger; // the selected trigger, so each field picker offers its fields
}) {
  const gatedTools = new Set(
    vocabulary.tools.filter((t) => !t.safe).map((t) => t.name),
  );

  const priorKeys = (i: number): string[] =>
    steps
      .slice(0, i)
      .map((s) => s.save_as)
      .filter((k): k is string => Boolean(k));

  const update = (i: number, step: Step) =>
    onChange(steps.map((s, j) => (j === i ? step : s)));
  const remove = (i: number) => onChange(steps.filter((_, j) => j !== i));
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= steps.length) return;
    const next = [...steps];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  const add = (type: StepType) => onChange([...steps, NEW_STEP[type]()]);

  return (
    <div className="space-y-2">
      {steps.map((step, i) => (
        <StepCard
          key={i}
          step={step}
          index={i}
          gatedTools={gatedTools}
          edit={{
            onChange: (s) => update(i, s),
            onRemove: () => remove(i),
            onMoveUp: () => move(i, -1),
            onMoveDown: () => move(i, 1),
            isFirst: i === 0,
            isLast: i === steps.length - 1,
            vocabulary,
            ctx: { vocabulary, trigger, contextKeys: priorKeys(i) },
          }}
        />
      ))}
      {steps.length === 0 && (
        <p className="rounded-lg border border-dashed p-4 text-center text-sm text-muted-foreground">
          No steps yet. Add the first thing this automation should do.
        </p>
      )}
      <AddStepMenu onAdd={add} />
    </div>
  );
}

function AddStepMenu({ onAdd }: { onAdd: (type: StepType) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <Button type="button" variant="outline" size="sm" onClick={() => setOpen((v) => !v)}>
        <Plus className="h-4 w-4" /> Add step
      </Button>
      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 w-56 overflow-hidden rounded-lg border bg-card shadow-lg">
          <p className="flex items-center gap-1.5 border-b px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            <ListOrdered className="h-3 w-3" /> Add a step
          </p>
          {ADD_OPTIONS.map(({ type, label, icon: Icon }) => (
            <button
              key={type}
              type="button"
              onClick={() => {
                onAdd(type);
                setOpen(false);
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] transition-colors hover:bg-muted"
            >
              <Icon className="h-4 w-4 text-muted-foreground" /> {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
