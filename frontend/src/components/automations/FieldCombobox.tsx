import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

// A themed field-path autocomplete (WS2) — replaces the native <datalist> ("weird
// triangle") in the condition editors. A controlled text input plus a popover that
// filters `suggestions` by the current dotted token; picking one fills the field.
// Free text is always allowed (suggestions guide, they don't constrain).
export function FieldCombobox({
  value,
  onChange,
  suggestions,
  placeholder = "entity.status",
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  suggestions: string[];
  placeholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  const q = value.trim().toLowerCase();
  const matches = (q
    ? suggestions.filter((s) => s.toLowerCase().includes(q) && s.toLowerCase() !== q)
    : suggestions
  ).slice(0, 8);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  useEffect(() => setActive(0), [value]);

  const choose = (s: string) => {
    onChange(s);
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      setOpen(true);
      return;
    }
    if (!open || matches.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (a + 1) % matches.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (a - 1 + matches.length) % matches.length);
    } else if (e.key === "Enter" && matches[active]) {
      e.preventDefault();
      choose(matches[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

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
      {open && matches.length > 0 && (
        <div className="absolute left-0 top-full z-30 mt-1 max-h-56 w-64 overflow-y-auto rounded-lg border bg-card py-1 shadow-lg">
          {matches.map((s, i) => (
            <button
              key={s}
              type="button"
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(s);
              }}
              className={cn(
                "block w-full px-3 py-1.5 text-left font-mono text-xs transition-colors",
                i === active ? "bg-muted text-foreground" : "text-muted-foreground",
              )}
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
