// The builder's field-scope logic — extracted from FieldPicker (Module 13) so the
// pure `fieldGroups` derivation is unit-testable without a browser and `FieldContext`
// is importable without pulling in a component file. FieldPicker/FieldCombobox
// re-import from here; FieldPicker re-exports the types for back-compat.
import type { Trigger } from "./recipe";
import type { Vocabulary } from "./api";

// The field context every template-accepting input needs: the vocabulary (for the
// catalog), the selected trigger (to filter to its fields), and this step's earlier
// save_as keys. One object threaded from the builder page down through the step
// tree, so the picker can offer the RIGHT fields everywhere with no prop sprawl.
export interface FieldContext {
  vocabulary: Vocabulary | null;
  trigger: Trigger;
  contextKeys: string[];
}

export interface FieldGroup {
  title: string;
  hint?: string; // shown instead of items (cron/manual, or an unlinked event)
  items: { path: string; label: string }[];
}

// The grouped, labeled fields for a context — shared by FieldPicker (template
// insertion) and FieldCombobox (the condition field side). Purely derived from the
// catalog + trigger; no vertical knowledge. EVERY available field appears: the
// core trigger fields, the selected event's payload fields, the mapped record's
// fields, and earlier step results. Cron/manual triggers start with no event, so
// their runs have no trigger/record fields at all — offering them would build a
// recipe that fails at run time, so those groups become an explanatory hint.
export function fieldGroups(ctx: FieldContext): FieldGroup[] {
  const vocab = ctx.vocabulary;
  const fc = vocab?.field_catalog;
  const isEvent = ctx.trigger.type === "event";
  const eventType = isEvent ? ctx.trigger.event_type || undefined : undefined;
  const groups: FieldGroup[] = [];

  if (!isEvent) {
    groups.push({
      title: "From the trigger",
      hint: "Scheduled and manual runs don't start from an event, so there are no trigger or record fields — use earlier step results.",
      items: [],
    });
  } else if (!fc) {
    // Older backend without the catalog: degrade to the flat suggestion list
    // (humanized client-side) so the menu is never empty.
    const flat = vocab?.field_suggestions ?? [];
    const asItem = (p: string) => ({ path: p, label: humanizePath(p) });
    groups.push({
      title: "From the trigger event",
      items: flat.filter((p) => p.startsWith("trigger.")).map(asItem),
    });
    groups.push({
      title: "The record",
      items: flat.filter((p) => p.startsWith("entity.")).map(asItem),
    });
  } else {
    const triggerItems = [...fc.trigger_fields];
    if (eventType) triggerItems.push(...(fc.payload_by_event[eventType] ?? []));
    groups.push({
      title: "From the trigger event",
      hint: eventType ? undefined : "Pick an event above to see everything it carries.",
      items: triggerItems,
    });

    const entityType = eventType ? fc.event_entity[eventType] : undefined;
    const entity = entityType ? fc.entities[entityType] : undefined;
    if (entity) {
      groups.push({ title: `The ${entity.label}`, items: entity.fields });
    } else {
      groups.push({
        title: "The record",
        hint: eventType
          ? "This event isn't linked to a specific record, so there are no record fields here."
          : "Pick an event above to see its record's fields.",
        items: [],
      });
    }
  }

  if (ctx.contextKeys.length > 0) {
    groups.push({
      title: "Earlier step results",
      items: ctx.contextKeys.map((k) => ({ path: `context.${k}`, label: `Step result: ${k}` })),
    });
  }
  return groups.filter((g) => g.items.length > 0 || g.hint);
}

// "trigger.payload.hours_per_week" -> "Hours per week" (fallback labeling when the
// backend catalog is unavailable — mirrors labelForPath's tail humanization).
export function humanizePath(path: string): string {
  const tail = path.split(".").pop() ?? path;
  const text = tail.replace(/_/g, " ").trim();
  return text ? text[0].toUpperCase() + text.slice(1) : path;
}
