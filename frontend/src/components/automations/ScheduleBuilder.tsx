// Schedule builder (WS4) — descriptive dropdowns that emit a standard 5-field cron
// expression, replacing the raw cron string + next-dates preview. The server still
// validates the expression (recipe.py `_validate_cron`) and schedules on it
// (`next_fire`); this is purely a friendlier way to author the common shapes.
import { Select, type SelectOption } from "@/components/ui/Select";
import { TimePicker } from "@/components/ui/TimePicker";

type Freq = "hourly" | "daily" | "weekday" | "weekly" | "monthly";

interface Schedule {
  freq: Freq;
  minute: number;
  hour: number;
  dow: number; // 0=Sun..6=Sat (weekly)
  dom: number; // 1..28 (monthly)
}

const DOW = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MINUTES = Array.from({ length: 12 }, (_, i) => i * 5); // 0,5,…,55
const DOMS = Array.from({ length: 28 }, (_, i) => i + 1); // 1..28 (safe every month)

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function buildCron(s: Schedule): string {
  switch (s.freq) {
    case "hourly":
      return `${s.minute} * * * *`;
    case "daily":
      return `${s.minute} ${s.hour} * * *`;
    case "weekday":
      return `${s.minute} ${s.hour} * * 1-5`;
    case "weekly":
      return `${s.minute} ${s.hour} * * ${s.dow}`;
    case "monthly":
      return `${s.minute} ${s.hour} ${s.dom} * *`;
  }
}

function parseCron(expr: string): Schedule {
  const base: Schedule = { freq: "daily", minute: 0, hour: 9, dow: 1, dom: 1 };
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return base;
  const [min, hour, dom, , dow] = parts;
  const asNum = (v: string, fallback: number) => {
    const n = Number(v);
    return Number.isInteger(n) ? n : fallback;
  };
  base.minute = asNum(min, 0);

  if (hour === "*") {
    return { ...base, freq: "hourly" };
  }
  base.hour = asNum(hour, 9);

  if (dow === "1-5") return { ...base, freq: "weekday" };
  if (dow !== "*" && /^[0-6]$/.test(dow)) return { ...base, freq: "weekly", dow: Number(dow) };
  if (dom !== "*" && /^\d+$/.test(dom)) {
    return { ...base, freq: "monthly", dom: Math.min(28, Math.max(1, Number(dom))) };
  }
  return { ...base, freq: "daily" };
}

const FREQ_OPTIONS: SelectOption<Freq>[] = [
  { value: "hourly", label: "Every hour" },
  { value: "daily", label: "Every day" },
  { value: "weekday", label: "Every weekday" },
  { value: "weekly", label: "Every week" },
  { value: "monthly", label: "Every month" },
];

const DOW_OPTIONS: SelectOption[] = DOW.map((d, i) => ({ value: String(i), label: d }));
const DOM_OPTIONS: SelectOption[] = DOMS.map((d) => ({ value: String(d), label: String(d) }));
const MINUTE_OPTIONS: SelectOption[] = MINUTES.map((m) => ({ value: String(m), label: `:${pad(m)}` }));

export function ScheduleBuilder({
  expression,
  onChange,
}: {
  expression: string;
  onChange: (expr: string) => void;
}) {
  const s = parseCron(expression);
  const emit = (patch: Partial<Schedule>) => onChange(buildCron({ ...s, ...patch }));

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        className="w-36"
        size="sm"
        value={s.freq}
        onChange={(v) => emit({ freq: v })}
        options={FREQ_OPTIONS}
        aria-label="Frequency"
      />

      {s.freq === "weekly" && (
        <>
          <span className="text-muted-foreground">on</span>
          <Select
            className="w-36"
            size="sm"
            value={String(s.dow)}
            onChange={(v) => emit({ dow: Number(v) })}
            options={DOW_OPTIONS}
            aria-label="Day of week"
          />
        </>
      )}

      {s.freq === "monthly" && (
        <>
          <span className="text-muted-foreground">on day</span>
          <Select
            className="w-20"
            size="sm"
            value={String(s.dom)}
            onChange={(v) => emit({ dom: Number(v) })}
            options={DOM_OPTIONS}
            aria-label="Day of month"
          />
        </>
      )}

      {s.freq === "hourly" ? (
        <>
          <span className="text-muted-foreground">at minute</span>
          <Select
            className="w-20"
            size="sm"
            value={String(s.minute)}
            onChange={(v) => emit({ minute: Number(v) })}
            options={MINUTE_OPTIONS}
            aria-label="Minute"
          />
        </>
      ) : (
        <>
          <span className="text-muted-foreground">at</span>
          <div className="w-36">
            <TimePicker
              value={`${pad(s.hour)}:${pad(s.minute)}`}
              onChange={(v) => {
                const [h, m] = v.split(":");
                emit({ hour: Number(h), minute: Number(m) });
              }}
            />
          </div>
        </>
      )}
    </div>
  );
}
