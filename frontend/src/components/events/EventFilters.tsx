import { X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { EventFacets, EventQuery } from "@/lib/api";

const selectClass =
  "h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function toLocalInput(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

function fromLocalInput(v: string): string | undefined {
  if (!v) return undefined;
  const d = new Date(v);
  return isNaN(d.getTime()) ? undefined : d.toISOString();
}

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

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <select
          className={selectClass}
          value={filters.source_system ?? ""}
          onChange={(e) => onChange({ source_system: e.target.value || undefined })}
        >
          <option value="">All sources</option>
          {facets.source_systems.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        <select
          className={selectClass}
          value={filters.event_type ?? ""}
          onChange={(e) => onChange({ event_type: e.target.value || undefined })}
        >
          <option value="">All event types</option>
          {facets.event_types.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>

        <label className="flex items-center gap-1 text-xs text-muted-foreground">
          From
          <input
            type="datetime-local"
            className={selectClass}
            value={toLocalInput(filters.since)}
            onChange={(e) => onChange({ since: fromLocalInput(e.target.value) })}
          />
        </label>
        <label className="flex items-center gap-1 text-xs text-muted-foreground">
          To
          <input
            type="datetime-local"
            className={selectClass}
            value={toLocalInput(filters.until)}
            onChange={(e) => onChange({ until: fromLocalInput(e.target.value) })}
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
