import { useEffect, useRef, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight, X } from "lucide-react";
import { cn } from "@/lib/utils";

// A themed date-time picker (WS6) — replaces the native <input type="datetime-local">
// whose calendar icon and popup don't follow the app theme. Dependency-free: a
// month grid + hour/minute selects styled from the palette tokens, correct in
// light + dark. Emits/accepts ISO strings (undefined = cleared).

const DOW = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
const MINUTES = Array.from({ length: 12 }, (_, i) => i * 5);
const HOURS = Array.from({ length: 24 }, (_, i) => i);

const selectClass =
  "h-8 rounded-md border border-input bg-background px-1.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

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
}: {
  value?: string;
  onChange: (iso: string | undefined) => void;
  placeholder?: string;
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
        className="flex h-9 items-center gap-2 rounded-md border border-input bg-background px-2.5 text-sm text-foreground transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <CalendarDays className="h-4 w-4 text-muted-foreground" />
        <span className={valid ? "" : "text-muted-foreground"}>
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
            className="ml-0.5 rounded-full text-muted-foreground hover:text-foreground"
            aria-label="Clear"
          >
            <X className="h-3.5 w-3.5" />
          </span>
        )}
      </button>

      {open && (
        <div className="absolute left-0 top-full z-40 mt-1 w-72 rounded-lg border bg-card p-3 shadow-lg">
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
              <span className="text-xs text-muted-foreground">Time</span>
              <select
                className={selectClass}
                value={hour}
                onChange={(e) => pickTime(Number(e.target.value), minute)}
              >
                {HOURS.map((h) => (
                  <option key={h} value={h}>{pad(h)}</option>
                ))}
              </select>
              <span className="text-xs text-muted-foreground">:</span>
              <select
                className={selectClass}
                value={minute}
                onChange={(e) => pickTime(hour, Number(e.target.value))}
              >
                {MINUTES.map((m) => (
                  <option key={m} value={m}>{pad(m)}</option>
                ))}
              </select>
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
