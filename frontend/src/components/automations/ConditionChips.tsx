import { Filter, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FieldCombobox } from "./FieldCombobox";
import {
  describeCondition,
  OPERATORS,
  type Condition,
  type Operator,
} from "@/lib/recipe";
import type { Vocabulary } from "@/lib/api";

const selectClass =
  "h-8 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

const NO_VALUE_OPS = new Set(["exists", "not_exists"]);

// The IF line. Read-mode renders the AND-list as chips; pass `onChange` to edit
// (field / operator / value rows). `label` toggles the leading "If" chip so the
// same component serves both the entry conditions and a step's nested conditions.
export function ConditionChips({
  conditions,
  onChange,
  vocabulary,
  contextKeys = [],
  label = "If",
  addLabel = "Add condition",
}: {
  conditions: Condition[];
  onChange?: (c: Condition[]) => void;
  vocabulary?: Vocabulary;
  contextKeys?: string[]; // save_as names from earlier steps -> context.<name>
  label?: string;
  addLabel?: string;
}) {
  const operators = (vocabulary?.operators as Operator[]) ?? OPERATORS;
  const roots = vocabulary?.field_roots ?? ["trigger", "entity", "context"];
  // Autocomplete suggestions: concrete server paths + this run's context keys.
  const suggestions = [
    ...roots.map((r) => `${r}.`),
    ...(vocabulary?.field_suggestions ?? []),
    ...contextKeys.map((k) => `context.${k}`),
  ];

  if (!onChange) {
    if (!conditions || conditions.length === 0) return null;
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <IfChip label={label} />
        {conditions.map((c, i) => (
          <span key={i} className="rounded-md border bg-card px-2 py-1 text-xs text-foreground">
            {describeCondition(c)}
          </span>
        ))}
      </div>
    );
  }

  const update = (i: number, patch: Partial<Condition>) =>
    onChange(conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const remove = (i: number) => onChange(conditions.filter((_, j) => j !== i));
  const add = () => onChange([...conditions, { field: `${roots[0]}.`, op: "eq", value: "" }]);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <IfChip label={label} />
        <span className="text-xs text-muted-foreground">
          all of these must be true
        </span>
      </div>
      {conditions.map((c, i) => (
        <div key={i} className="flex flex-wrap items-center gap-1.5">
          <FieldCombobox
            value={c.field}
            onChange={(field) => update(i, { field })}
            suggestions={suggestions}
          />
          <select
            className={selectClass}
            value={c.op}
            onChange={(e) => update(i, { op: e.target.value })}
          >
            {operators.map((op) => (
              <option key={op} value={op}>
                {op}
              </option>
            ))}
          </select>
          {!NO_VALUE_OPS.has(c.op) && (
            <Input
              value={String(c.value ?? "")}
              onChange={(e) => update(i, { value: e.target.value })}
              placeholder="value"
              className="h-8 w-40 text-xs"
            />
          )}
          <button
            type="button"
            onClick={() => remove(i)}
            className="text-muted-foreground hover:text-destructive"
            aria-label="Remove condition"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      ))}
      <Button type="button" size="sm" variant="outline" onClick={add}>
        <Plus className="h-3.5 w-3.5" /> {addLabel}
      </Button>
    </div>
  );
}

function IfChip({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
      <Filter className="h-3 w-3" /> {label}
    </span>
  );
}
