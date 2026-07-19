import { useEffect, useRef, useState } from "react";
import { Clock } from "lucide-react";
import { cn } from "@/lib/utils";
import { Select, type SelectOption } from "@/components/ui/Select";

// A themed time picker (Module 13) — the sibling of DateTimePicker, replacing the
// native <input type="time"> whose clock popup doesn't follow the app theme.
// Dependency-free: a button + a small popover with 12-hour / minute / AM-PM
// Selects. Emits and accepts a 24-hour "HH:MM" string (the shape the schedule and
// availability forms already send), so it's a drop-in for the native time input.

const MINUTES = Array.from({ length: 12 }, (_, i) => i * 5); // 0,5,…,55
const HOURS12 = Array.from({ length: 12 }, (_, i) => i + 1); // 1..12

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

const HOUR_OPTIONS: SelectOption[] = HOURS12.map((h) => ({ value: String(h), label: String(h) }));
const MINUTE_OPTIONS: SelectOption[] = MINUTES.map((m) => ({ value: String(m), label: pad(m) }));
const PERIOD_OPTIONS: SelectOption<"AM" | "PM">[] = [
  { value: "AM", label: "AM" },
  { value: "PM", label: "PM" },
];

// Parse "HH:MM" (24h) → { hour24, minute }; tolerant of an empty/garbled value.
function parse(value: string): { hour24: number; minute: number } | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  if (!m) return null;
  const hour24 = Number(m[1]);
  const minute = Number(m[2]);
  if (hour24 < 0 || hour24 > 23 || minute < 0 || minute > 59) return null;
  return { hour24, minute };
}

function to12(hour24: number): { hour12: number; period: "AM" | "PM" } {
  const period = hour24 < 12 ? "AM" : "PM";
  const hour12 = hour24 % 12 === 0 ? 12 : hour24 % 12;
  return { hour12, period };
}

function to24(hour12: number, period: "AM" | "PM"): number {
  const base = hour12 % 12; // 12 -> 0
  return period === "PM" ? base + 12 : base;
}

function label12(hour24: number, minute: number): string {
  const { hour12, period } = to12(hour24);
  return `${hour12}:${pad(minute)} ${period}`;
}

export function TimePicker({
  value,
  onChange,
  placeholder = "Pick a time",
  align = "start",
}: {
  value: string; // "HH:MM" (24h) or ""
  onChange: (value: string) => void;
  placeholder?: string;
  align?: "start" | "end";
}) {
  const parsed = parse(value);
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

  const hour24 = parsed?.hour24 ?? 9;
  // Snap an off-grid minute to the nearest 5 for the picker (defaults are on-grid).
  const minute = parsed ? (Math.round(parsed.minute / 5) * 5) % 60 : 0;
  const { hour12, period } = to12(hour24);

  const emit = (h12: number, m: number, p: "AM" | "PM") =>
    onChange(`${pad(to24(h12, p))}:${pad(m)}`);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex h-9 w-full items-center gap-2 rounded-md border border-input bg-background px-2.5 text-sm text-foreground transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Clock className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className={cn("truncate", parsed ? "" : "text-muted-foreground")}>
          {parsed ? label12(hour24, minute) : placeholder}
        </span>
      </button>

      {open && (
        <div
          className={cn(
            "absolute top-full z-40 mt-1 flex items-center gap-1.5 rounded-lg border bg-card p-2 shadow-lg",
            align === "end" ? "right-0" : "left-0",
          )}
        >
          <Select
            className="w-16"
            size="sm"
            value={String(hour12)}
            onChange={(v) => emit(Number(v), minute, period)}
            options={HOUR_OPTIONS}
            aria-label="Hour"
          />
          <span className="text-xs text-muted-foreground">:</span>
          <Select
            className="w-16"
            size="sm"
            value={String(minute)}
            onChange={(v) => emit(hour12, Number(v), period)}
            options={MINUTE_OPTIONS}
            aria-label="Minute"
          />
          <Select<"AM" | "PM">
            className="w-20"
            size="sm"
            value={period}
            onChange={(v) => emit(hour12, minute, v)}
            options={PERIOD_OPTIONS}
            aria-label="AM or PM"
          />
        </div>
      )}
    </div>
  );
}
