import { X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { DateTimePicker } from "@/components/ui/DateTimePicker";
import { Select, type SelectOption } from "@/components/ui/Select";
import {
  eventTypeLabel,
  isDisplayableEventType,
  isDisplayableSource,
  sourceLabel,
} from "@/lib/recipe";
import type { EventFacets, EventQuery } from "@/lib/api";

export function EventFilters({
  facets,
  filters,
  onChange,
}: {
  facets: EventFacets;
  filters: EventQuery;
  onChange: (patch: Partial<EventQuery>) => void;
}) {
  const hasEntity = Boolean(filters.entity_type && filters.entity_id);

  // Labeled options; filtering stays keyed by the raw value (label is a view only).
  // Test/dummy noise from the observed facets is hidden.
  const sourceOptions: SelectOption[] = facets.source_systems
    .filter(isDisplayableSource)
    .map((s) => ({ value: s, label: sourceLabel(s) }));
  const eventTypeOptions: SelectOption[] = facets.event_types
    .filter(isDisplayableEventType)
    .map((t) => ({ value: t, label: eventTypeLabel(t), mono: t }));

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select
          className="w-44"
          value={filters.source_system ?? ""}
          onChange={(v) => onChange({ source_system: v || undefined })}
          options={sourceOptions}
          clearable
          placeholder="All sources"
          aria-label="Source filter"
        />

        <Select
          className="w-56"
          value={filters.event_type ?? ""}
          onChange={(v) => onChange({ event_type: v || undefined })}
          options={eventTypeOptions}
          clearable
          searchable
          placeholder="All event types"
          aria-label="Event type filter"
        />

        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          From
          <DateTimePicker
            value={filters.since}
            onChange={(iso) => onChange({ since: iso })}
            placeholder="Any start"
          />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          To
          <DateTimePicker
            value={filters.until}
            onChange={(iso) => onChange({ until: iso })}
            placeholder="Any end"
          />
        </label>
      </div>

      {hasEntity && (
        <div className="flex items-center gap-2">
          <Badge variant="secondary" className="gap-1">
            Filtered to {filters.entity_type} ·{" "}
            <span className="font-mono">{filters.entity_id!.slice(0, 8)}</span>
            <button
              onClick={() => onChange({ entity_type: undefined, entity_id: undefined })}
              className="ml-1 rounded-full hover:text-foreground"
              aria-label="Clear entity filter"
            >
              <X className="h-3 w-3" />
            </button>
          </Badge>
        </div>
      )}
    </div>
  );
}
