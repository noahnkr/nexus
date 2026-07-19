import { useEffect, useRef, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Select, type SelectOption } from "@/components/ui/Select";

// A themed date-time picker (WS6) — replaces the native <input type="datetime-local">
// whose calendar icon and popup don't follow the app theme. Dependency-free: a
// month grid + hour/minute selects styled from the palette tokens, correct in
// light + dark. Emits/accepts ISO strings (undefined = cleared).

const DOW = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
const MINUTES = Array.from({ length: 12 }, (_, i) => i * 5);
const HOURS12 = Array.from({ length: 12 }, (_, i) => i + 1); // 1..12

function pad(n: number): string {
  return String(n).padStart(2, "0");
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

const HOUR12_OPTIONS: SelectOption[] = HOURS12.map((h) => ({ value: String(h), label: String(h) }));
const MINUTE_OPTIONS: SelectOption[] = MINUTES.map((m) => ({ value: String(m), label: pad(m) }));
const PERIOD_OPTIONS: SelectOption<"AM" | "PM">[] = [
  { value: "AM", label: "AM" },
  { value: "PM", label: "PM" },
];

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

function formatLabel(d: Date): string {
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function DateTimePicker({
  value,
  onChange,
  placeholder = "Any time",
  className,
  align = "left",
}: {
  value?: string;
  onChange: (iso: string | undefined) => void;
  placeholder?: string;
  // Trigger classes — pass "w-full" to make it fill a form column (the default is
  // content-width, which is what a filter bar wants).
  className?: string;
  // Which edge the popover hangs from. Right-align it when the field sits in the
  // right-hand column of a narrow container, or the 320px panel overflows.
  align?: "left" | "right";
}) {
  const selected = value ? new Date(value) : null;
  const valid = selected && !isNaN(selected.getTime()) ? selected : null;

  const [open, setOpen] = useState(false);
  const [view, setView] = useState<Date>(() => valid ?? new Date());
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const hour = valid ? valid.getHours() : 9;
  const minute = valid ? Math.round(valid.getMinutes() / 5) * 5 : 0;

  const emit = (year: number, month: number, day: number, h: number, m: number) => {
    onChange(new Date(year, month, day, h, m, 0, 0).toISOString());
  };

  const pickDay = (day: number) => {
    emit(view.getFullYear(), view.getMonth(), day, hour, minute);
  };
  const pickTime = (h: number, m: number) => {
    const base = valid ?? new Date(view.getFullYear(), view.getMonth(), view.getDate());
    emit(base.getFullYear(), base.getMonth(), base.getDate(), h, m);
  };

  const year = view.getFullYear();
  const month = view.getMonth();
  const startDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells: (number | null)[] = [
    ...Array.from({ length: startDay }, () => null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ];

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex h-9 items-center gap-2 rounded-md border border-input bg-background px-2.5 text-sm text-foreground transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          className,
        )}
      >
        <CalendarDays className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span
          className={cn(
            "flex-1 truncate text-left",
            valid ? "" : "text-muted-foreground",
          )}
        >
          {valid ? formatLabel(valid) : placeholder}
        </span>
        {valid && (
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => {
              e.stopPropagation();
              onChange(undefined);
            }}
            className="ml-0.5 shrink-0 rounded-full text-muted-foreground hover:text-foreground"
            aria-label="Clear"
          >
            <X className="h-3.5 w-3.5" />
          </span>
        )}
      </button>

      {open && (
        <div
          className={cn(
            "absolute top-full z-40 mt-1 w-80 rounded-lg border bg-card p-3 shadow-lg",
            align === "right" ? "right-0" : "left-0",
          )}
        >
          <div className="mb-2 flex items-center justify-between">
            <button
              type="button"
              onClick={() => setView(new Date(year, month - 1, 1))}
              className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label="Previous month"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="text-sm font-medium">
              {view.toLocaleString(undefined, { month: "long", year: "numeric" })}
            </span>
            <button
              type="button"
              onClick={() => setView(new Date(year, month + 1, 1))}
              className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label="Next month"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>

          <div className="mb-1 grid grid-cols-7 gap-0.5">
            {DOW.map((d) => (
              <span key={d} className="py-1 text-center text-[10px] font-medium text-muted-foreground">
                {d}
              </span>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-0.5">
            {cells.map((day, i) =>
              day === null ? (
                <span key={`b${i}`} />
              ) : (
                <button
                  key={day}
                  type="button"
                  onClick={() => pickDay(day)}
                  className={cn(
                    "flex h-8 items-center justify-center rounded-md text-xs transition-colors",
                    valid && sameDay(valid, new Date(year, month, day))
                      ? "bg-primary text-primary-foreground"
                      : "text-foreground hover:bg-muted",
                  )}
                >
                  {day}
                </button>
              ),
            )}
          </div>

          <div className="mt-3 flex items-center justify-between border-t pt-3">
            <div className="flex items-center gap-1.5">
              <Select
                className="w-16"
                size="sm"
                value={String(to12(hour).hour12)}
                onChange={(v) => pickTime(to24(Number(v), to12(hour).period), minute)}
                options={HOUR12_OPTIONS}
                aria-label="Hour"
              />
              <span className="text-xs text-muted-foreground">:</span>
              <Select
                className="w-16"
                size="sm"
                value={String(minute)}
                onChange={(v) => pickTime(hour, Number(v))}
                options={MINUTE_OPTIONS}
                aria-label="Minute"
              />
              <Select<"AM" | "PM">
                className="w-20"
                size="sm"
                value={to12(hour).period}
                onChange={(v) => pickTime(to24(to12(hour).hour12, v), minute)}
                options={PERIOD_OPTIONS}
                aria-label="AM or PM"
              />
            </div>
            <button
              type="button"
              onClick={() => {
                const now = new Date();
                emit(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours(), Math.round(now.getMinutes() / 5) * 5);
                setView(now);
              }}
              className="text-xs text-primary hover:underline"
            >
              Now
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
