import { Zap } from "lucide-react";
import { ScheduleBuilder } from "./ScheduleBuilder";
import type { Vocabulary } from "@/lib/api";
import { describeTrigger, sourceLabel, type Trigger, type TriggerType } from "@/lib/recipe";

const selectClass =
  "h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

// The WHEN line. Read-mode by default (a plain sentence); pass `onChange` +
// `vocabulary` to turn it into an editor (trigger type + event/cron fields). One
// component, two modes — read and write share the tree.
export function TriggerSentence({
  trigger,
  onChange,
  vocabulary,
}: {
  trigger: Trigger;
  onChange?: (t: Trigger) => void;
  vocabulary?: Vocabulary;
}) {
  if (!onChange || !vocabulary) {
    return (
      <div className="flex items-center gap-2 text-sm">
        <WhenChip />
        <span className="text-foreground">{describeTrigger(trigger)}</span>
      </div>
    );
  }

  const setType = (type: TriggerType) => {
    if (type === "event") onChange({ type: "event", event_type: "", source_system: null });
    else if (type === "cron") onChange({ type: "cron", expression: "0 9 * * 1" });
    else onChange({ type: "manual" });
  };

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <WhenChip />
      <select
        className={selectClass}
        value={trigger.type}
        onChange={(e) => setType(e.target.value as TriggerType)}
      >
        <option value="event">an event happens</option>
        <option value="cron">on a schedule</option>
        <option value="manual">run manually</option>
      </select>

      {trigger.type === "event" && (
        <>
          <select
            className={selectClass}
            value={trigger.event_type ?? ""}
            onChange={(e) => onChange({ ...trigger, event_type: e.target.value })}
          >
            <option value="">select event…</option>
            {vocabulary.triggers.event_types.map((et) => (
              <option key={et} value={et}>
                {et}
              </option>
            ))}
          </select>
          <span className="text-muted-foreground">from</span>
          <select
            className={selectClass}
            value={trigger.source_system ?? ""}
            onChange={(e) =>
              onChange({ ...trigger, source_system: e.target.value || null })
            }
          >
            <option value="">any source</option>
            {vocabulary.triggers.source_systems.map((s) => (
              <option key={s} value={s}>
                {sourceLabel(s)}
              </option>
            ))}
          </select>
        </>
      )}

      {trigger.type === "cron" && (
        <ScheduleBuilder
          expression={trigger.expression ?? "0 9 * * 1"}
          onChange={(expr) => onChange({ type: "cron", expression: expr })}
        />
      )}
    </div>
  );
}

function WhenChip() {
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-primary/10 px-2 py-1 text-xs font-semibold uppercase tracking-wide text-primary">
      <Zap className="h-3 w-3" /> When
    </span>
  );
}
