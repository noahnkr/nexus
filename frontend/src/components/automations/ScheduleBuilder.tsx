// Schedule builder (WS4) — descriptive dropdowns that emit a standard 5-field cron
// expression, replacing the raw cron string + next-dates preview. The server still
// validates the expression (recipe.py `_validate_cron`) and schedules on it
// (`next_fire`); this is purely a friendlier way to author the common shapes.

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
const HOURS = Array.from({ length: 24 }, (_, i) => i);
const DOMS = Array.from({ length: 28 }, (_, i) => i + 1); // 1..28 (safe every month)

const selectClass =
  "h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function hourLabel(h: number): string {
  const period = h < 12 ? "AM" : "PM";
  const twelve = h % 12 === 0 ? 12 : h % 12;
  return `${twelve} ${period}`;
}

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
      <select
        className={selectClass}
        value={s.freq}
        onChange={(e) => emit({ freq: e.target.value as Freq })}
      >
        <option value="hourly">Every hour</option>
        <option value="daily">Every day</option>
        <option value="weekday">Every weekday</option>
        <option value="weekly">Every week</option>
        <option value="monthly">Every month</option>
      </select>

      {s.freq === "weekly" && (
        <>
          <span className="text-muted-foreground">on</span>
          <select
            className={selectClass}
            value={s.dow}
            onChange={(e) => emit({ dow: Number(e.target.value) })}
          >
            {DOW.map((d, i) => (
              <option key={d} value={i}>{d}</option>
            ))}
          </select>
        </>
      )}

      {s.freq === "monthly" && (
        <>
          <span className="text-muted-foreground">on day</span>
          <select
            className={selectClass}
            value={s.dom}
            onChange={(e) => emit({ dom: Number(e.target.value) })}
          >
            {DOMS.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        </>
      )}

      {s.freq === "hourly" ? (
        <>
          <span className="text-muted-foreground">at minute</span>
          <select
            className={selectClass}
            value={s.minute}
            onChange={(e) => emit({ minute: Number(e.target.value) })}
          >
            {MINUTES.map((m) => (
              <option key={m} value={m}>:{pad(m)}</option>
            ))}
          </select>
        </>
      ) : (
        <>
          <span className="text-muted-foreground">at</span>
          <select
            className={selectClass}
            value={s.hour}
            onChange={(e) => emit({ hour: Number(e.target.value) })}
          >
            {HOURS.map((h) => (
              <option key={h} value={h}>{hourLabel(h)}</option>
            ))}
          </select>
          <select
            className={selectClass}
            value={s.minute}
            onChange={(e) => emit({ minute: Number(e.target.value) })}
          >
            {MINUTES.map((m) => (
              <option key={m} value={m}>:{pad(m)}</option>
            ))}
          </select>
        </>
      )}
    </div>
  );
}
