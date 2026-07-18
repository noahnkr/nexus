import { useEffect, useMemo, useRef, useState } from "react";
import { Braces } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Trigger } from "@/lib/recipe";
import type { Vocabulary } from "@/lib/api";
import { TokenText, type TokenTextHandle } from "./TokenText";

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
function humanizePath(path: string): string {
  const tail = path.split(".").pop() ?? path;
  const text = tail.replace(/_/g, " ").trim();
  return text ? text[0].toUpperCase() + text.slice(1) : path;
}

// A template-accepting input: the TokenText chip editor + a FieldPicker that inserts
// the picked `{{path}}` at the caret. The one composed control every string field in
// the builder uses, so the picker/chip behavior is identical everywhere.
export function TokenField({
  value,
  onChange,
  ctx,
  multiline,
  placeholder,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  ctx: FieldContext;
  multiline?: boolean;
  placeholder?: string;
  className?: string;
}) {
  const inputRef = useRef<TokenTextHandle>(null);
  return (
    <div className={cn("flex gap-1.5", multiline ? "items-start" : "items-center")}>
      <TokenText
        ref={inputRef}
        value={value}
        onChange={onChange}
        catalog={ctx.vocabulary?.field_catalog}
        contextKeys={ctx.contextKeys}
        multiline={multiline}
        placeholder={placeholder}
        className={cn("flex-1", className)}
      />
      <FieldPicker ctx={ctx} onPick={(p) => inputRef.current?.insertToken(p)} />
    </div>
  );
}


// A popover that inserts a `{{path}}` field at the caret (via the input's
// insertToken ref) — grouped and labeled by the selected trigger's actual fields.
// A "Custom path…" footer keeps the free-text escape hatch for power users.
export function FieldPicker({
  ctx,
  onPick,
}: {
  ctx: FieldContext;
  onPick: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const groups = useMemo(() => fieldGroups(ctx), [ctx]);
  const q = query.trim().toLowerCase();
  const filtered = q
    ? groups
        .map((g) => ({
          ...g,
          items: g.items.filter(
            (it) => it.label.toLowerCase().includes(q) || it.path.toLowerCase().includes(q),
          ),
        }))
        .filter((g) => g.items.length > 0)
    : groups;

  const pick = (path: string) => {
    onPick(path);
    setOpen(false);
    setQuery("");
  };

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 items-center gap-1 rounded-md border border-input px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        title="Insert a field"
      >
        <Braces className="h-3.5 w-3.5" />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-1 w-72 overflow-hidden rounded-lg border bg-card shadow-lg">
          <div className="border-b p-2">
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search fields…"
              className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </div>
          <div className="max-h-64 overflow-y-auto py-1">
            {filtered.map((g) => (
              <div key={g.title} className="px-1 py-1">
                <p className="px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  {g.title}
                </p>
                {g.hint && (
                  <p className="px-2 pb-1 text-[12px] italic text-muted-foreground/80">{g.hint}</p>
                )}
                {g.items.map((it) => (
                  <button
                    key={it.path}
                    type="button"
                    onClick={() => pick(it.path)}
                    className="flex w-full flex-col items-start rounded px-2 py-1 text-left transition-colors hover:bg-muted"
                  >
                    <span className="text-[13px] text-foreground">{it.label}</span>
                    <span className="font-mono text-[10px] text-muted-foreground">{it.path}</span>
                  </button>
                ))}
              </div>
            ))}
            {filtered.length === 0 && (
              <p className="px-3 py-2 text-[12px] text-muted-foreground">No matching fields.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
