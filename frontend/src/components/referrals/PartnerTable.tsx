import { ChevronDown, ChevronUp, Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  categoryMeta,
  fmtHoursWon,
  fmtRate,
  type SortDir,
  type SortKey,
} from "@/lib/referrals";
import { cn, relativeTime } from "@/lib/utils";
import type { ReferralSourceRow } from "@/lib/api";
import { MonthlyTrendBars } from "./MonthlyTrendBars";

// One row per distinct lead source. A tracked source shows its category chip; an
// untracked one shows a muted state + a Track button that opens the create dialog
// prefilled with the source name. Headers sort client-side (all data is in the one
// metrics call). Row click opens the partner drawer.
function SortHeader({
  label,
  sortKey,
  sort,
  onSort,
  align = "right",
}: {
  label: string;
  sortKey: SortKey;
  sort: { key: SortKey; dir: SortDir };
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
}) {
  const active = sort.key === sortKey;
  const Icon = active && sort.dir === "asc" ? ChevronUp : ChevronDown;
  return (
    <TableHead className={align === "right" ? "text-right" : undefined}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cn(
          "inline-flex items-center gap-1 transition-colors hover:text-foreground",
          align === "right" && "flex-row-reverse",
          active ? "text-foreground" : "text-muted-foreground",
        )}
      >
        {label}
        <Icon className={cn("h-3.5 w-3.5", active ? "opacity-100" : "opacity-30")} />
      </button>
    </TableHead>
  );
}

export function PartnerTable({
  rows,
  months,
  sort,
  onSort,
  onSelectRow,
  onTrack,
}: {
  rows: ReferralSourceRow[];
  months: string[];
  sort: { key: SortKey; dir: SortDir };
  onSort: (key: SortKey) => void;
  onSelectRow: (row: ReferralSourceRow) => void;
  onTrack: (source: string) => void;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <SortHeader label="Source" sortKey="source" sort={sort} onSort={onSort} align="left" />
            <SortHeader label="Leads" sortKey="leads_total" sort={sort} onSort={onSort} />
            <SortHeader label="Converted" sortKey="converted" sort={sort} onSort={onSort} />
            <SortHeader label="Conversion" sortKey="conversion_rate" sort={sort} onSort={onSort} />
            <SortHeader label="Hours won" sortKey="hours_won" sort={sort} onSort={onSort} />
            <SortHeader label="Last lead" sortKey="last_lead_at" sort={sort} onSort={onSort} />
            <TableHead className="text-right">6-mo trend</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => {
            const meta = row.partner ? categoryMeta(row.partner.category) : null;
            return (
              <TableRow
                key={row.source}
                className="cursor-pointer"
                onClick={() => onSelectRow(row)}
              >
                <TableCell>
                  <div className="flex flex-col gap-1">
                    <span className="font-medium">{row.source}</span>
                    {meta ? (
                      <Badge variant="outline" className="w-fit gap-1.5 font-normal">
                        <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} />
                        {meta.label}
                      </Badge>
                    ) : (
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">Untracked</span>
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-6 px-2 text-xs"
                          onClick={(e) => {
                            e.stopPropagation();
                            onTrack(row.source);
                          }}
                        >
                          <Plus className="h-3 w-3" /> Track
                        </Button>
                      </div>
                    )}
                  </div>
                </TableCell>
                <TableCell className="text-right tabular-nums">{row.leads_total}</TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {row.converted}
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {row.leads_total > 0 ? fmtRate(row.conversion_rate) : "—"}
                </TableCell>
                <TableCell className="text-right tabular-nums font-medium">
                  {row.hours_won > 0 ? fmtHoursWon(row.hours_won) : "—"}
                </TableCell>
                <TableCell className="text-right text-muted-foreground">
                  {row.last_lead_at ? relativeTime(row.last_lead_at) : "—"}
                </TableCell>
                <TableCell>
                  <div className="ml-auto w-28">
                    <MonthlyTrendBars monthly={row.monthly} months={months} variant="spark" />
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
