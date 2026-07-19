import { Zap } from "lucide-react";
import { ScheduleBuilder } from "./ScheduleBuilder";
import { Select, type SelectOption } from "@/components/ui/Select";
import type { Vocabulary } from "@/lib/api";
import {
  describeTrigger,
  eventTypeLabel,
  isDisplayableEventType,
  isDisplayableSource,
  sourceLabel,
  type Trigger,
  type TriggerType,
} from "@/lib/recipe";

// Trigger-type options (Module 13). No icons — the WHEN chip already carries the
// zap, and an icon per option just repeats it.
const TYPE_OPTIONS: SelectOption<TriggerType>[] = [
  { value: "event", label: "an event happens" },
  { value: "cron", label: "on a schedule" },
  { value: "manual", label: "run manually" },
];

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

  // Plain label + raw mono type for every offerable event (Module 13); test/dummy
  // noise from the observed facets is filtered out.
  const eventOptions: SelectOption[] = vocabulary.triggers.event_types
    .filter(isDisplayableEventType)
    .map((et) => ({ value: et, label: eventTypeLabel(et), mono: et }));

  const sourceOptions: SelectOption[] = vocabulary.triggers.source_systems
    .filter(isDisplayableSource)
    .map((s) => ({ value: s, label: sourceLabel(s) }));

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <WhenChip />
      <Select
        className="w-48"
        value={trigger.type}
        onChange={setType}
        options={TYPE_OPTIONS}
        aria-label="Trigger type"
      />

      {trigger.type === "event" && (
        // Keep event + "from" + source together so the source select never wraps to
        // its own line (the group wraps as a unit if the row is too narrow).
        <div className="flex items-center gap-2">
          <Select
            className="w-56"
            value={trigger.event_type ?? ""}
            onChange={(v) => onChange({ ...trigger, event_type: v })}
            options={eventOptions}
            placeholder="select event…"
            searchable
            aria-label="Event type"
          />
          <span className="shrink-0 text-muted-foreground">from</span>
          <Select
            className="w-44"
            value={trigger.source_system ?? ""}
            onChange={(v) => onChange({ ...trigger, source_system: v || null })}
            options={sourceOptions}
            placeholder="any source"
            clearable
            aria-label="Source system"
          />
        </div>
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
