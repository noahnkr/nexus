import { useEffect, useMemo, useRef, useState } from "react";
import { Braces } from "lucide-react";
import { cn } from "@/lib/utils";
import { fieldGroups, type FieldContext } from "@/lib/fields";
import { TokenText, type TokenTextHandle } from "./TokenText";

// `fieldGroups`, `FieldContext`, and `FieldGroup` moved to lib/fields.ts (Module 13,
// so the scope logic is unit-testable). Re-exported here for back-compat — existing
// call sites import these from FieldPicker.
export { fieldGroups } from "@/lib/fields";
export type { FieldContext, FieldGroup } from "@/lib/fields";

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
