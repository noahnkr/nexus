import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { fieldGroups, type FieldContext } from "./FieldPicker";

// The condition FIELD side — a dotted path (not a template). A controlled text input
// plus a popover of grouped, labeled fields from the catalog (Module 11b): label
// primary, mono path secondary. Picking one fills the field; free text is always
// allowed (suggestions guide, they don't constrain). Keyboard: arrows/enter/escape.
export function FieldCombobox({
  value,
  onChange,
  ctx,
  placeholder = "pick a field",
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  ctx: FieldContext;
  placeholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  // Flat, labeled entries grouped by source; filtered by the current text.
  const groups = useMemo(() => fieldGroups(ctx), [ctx]);
  const q = value.trim().toLowerCase();
  const shownGroups = groups
    .map((g) => ({
      title: g.title,
      items: g.items.filter(
        (it) =>
          !q || it.label.toLowerCase().includes(q) || it.path.toLowerCase().includes(q),
      ),
    }))
    .filter((g) => g.items.length > 0);
  const flat = shownGroups.flatMap((g) => g.items);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  useEffect(() => setActive(0), [value]);

  const choose = (path: string) => {
    onChange(path);
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      setOpen(true);
      return;
    }
    if (!open || flat.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (a + 1) % flat.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (a - 1 + flat.length) % flat.length);
    } else if (e.key === "Enter" && flat[active]) {
      e.preventDefault();
      choose(flat[active].path);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  let flatIndex = -1;
  return (
    <div ref={ref} className="relative">
      <input
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        className={cn(
          "h-8 w-48 rounded-md border border-input bg-background px-2 font-mono text-xs shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          className,
        )}
      />
      {open && flat.length > 0 && (
        <div className="absolute left-0 top-full z-30 mt-1 max-h-64 w-72 overflow-y-auto rounded-lg border bg-card py-1 shadow-lg">
          {shownGroups.map((g) => (
            <div key={g.title}>
              <p className="px-3 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {g.title}
              </p>
              {g.items.map((it) => {
                flatIndex += 1;
                const i = flatIndex;
                return (
                  <button
                    key={it.path}
                    type="button"
                    onMouseEnter={() => setActive(i)}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      choose(it.path);
                    }}
                    className={cn(
                      "flex w-full flex-col items-start px-3 py-1 text-left transition-colors",
                      i === active ? "bg-muted" : "",
                    )}
                  >
                    <span className="text-[13px] text-foreground">{it.label}</span>
                    <span className="font-mono text-[10px] text-muted-foreground">{it.path}</span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
