import { Filter, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select, type SelectOption } from "@/components/ui/Select";
import { FieldCombobox } from "./FieldCombobox";
import { TokenField, type FieldContext } from "./FieldPicker";
import {
  describeCondition,
  operatorLabel,
  OPERATORS,
  type Condition,
  type Operator,
} from "@/lib/recipe";
import type { FieldCatalog } from "@/lib/api";

const NO_VALUE_OPS = new Set(["exists", "not_exists"]);

// The IF line. Read-mode renders the AND-list as chips; pass `onChange` + `ctx` to
// edit (field / operator / value rows). The field side is a catalog-grouped combobox
// (a path); the value side is a token input (Module 11a made condition values
// render templates). `label` toggles the leading chip so the same component serves
// both the entry conditions and a step's nested conditions.
export function ConditionChips({
  conditions,
  onChange,
  ctx,
  catalog: catalogProp,
  label = "If",
  addLabel = "Add condition",
}: {
  conditions: Condition[];
  onChange?: (c: Condition[]) => void;
  ctx?: FieldContext;
  catalog?: FieldCatalog; // read-mode label source when no ctx is threaded
  label?: string;
  addLabel?: string;
}) {
  const catalog = catalogProp ?? ctx?.vocabulary?.field_catalog;
  const operators = (ctx?.vocabulary?.operators as Operator[]) ?? OPERATORS;

  if (!onChange) {
    if (!conditions || conditions.length === 0) return null;
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <IfChip label={label} />
        {conditions.map((c, i) => (
          <span key={i} className="rounded-md border bg-card px-2 py-1 text-xs text-foreground">
            {describeCondition(c, catalog)}
          </span>
        ))}
      </div>
    );
  }

  const update = (i: number, patch: Partial<Condition>) =>
    onChange(conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const remove = (i: number) => onChange(conditions.filter((_, j) => j !== i));
  const add = () => onChange([...conditions, { field: "", op: "eq", value: "" }]);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <IfChip label={label} />
        <span className="text-xs text-muted-foreground">all of these must be true</span>
      </div>
      {conditions.map((c, i) => (
        <div key={i} className="flex flex-wrap items-center gap-1.5">
          <FieldCombobox
            value={c.field}
            onChange={(field) => update(i, { field })}
            ctx={ctx ?? { vocabulary: null, trigger: { type: "manual" }, contextKeys: [] }}
          />
          <Select
            className="w-44"
            size="sm"
            value={c.op}
            onChange={(op) => update(i, { op })}
            options={operators.map(
              (op): SelectOption => ({ value: op, label: operatorLabel(op) }),
            )}
            aria-label="Operator"
          />
          {!NO_VALUE_OPS.has(c.op) && (
            <div className="w-52">
              <TokenField
                value={String(c.value ?? "")}
                onChange={(v) => update(i, { value: v })}
                ctx={ctx ?? { vocabulary: null, trigger: { type: "manual" }, contextKeys: [] }}
                placeholder="value or a field"
              />
            </div>
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
