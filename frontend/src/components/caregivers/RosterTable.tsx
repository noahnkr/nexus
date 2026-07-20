import type { RosterCaregiver } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  fmtHours,
  fmtUtilization,
  isOverbooked,
  resourceStatusMeta,
  utilizationBarPct,
} from "@/lib/workforce";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { CredentialBadges } from "@/components/caregivers/CredentialBadges";

// One row per caregiver — active first, inactive muted (sortRoster does the order
// upstream). Every number is server-computed; this file only formats and tones.
function UtilizationBar({ pct }: { pct: number | null }) {
  if (pct == null) {
    return (
      <span className="text-xs text-muted-foreground" title="No declared availability">
        —
      </span>
    );
  }
  const over = isOverbooked(pct);
  return (
    <div className="flex items-center gap-2">
      <div
        className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-muted"
        role="presentation"
      >
        <div
          className={cn("h-full rounded-full", over ? "bg-warning" : "bg-primary")}
          style={{ width: `${utilizationBarPct(pct)}%` }}
        />
      </div>
      <span
        className={cn(
          "tabular-nums text-xs",
          over ? "font-medium text-warning" : "text-muted-foreground",
        )}
      >
        {fmtUtilization(pct)}
      </span>
    </div>
  );
}

export function RosterTable({
  caregivers,
  onOpen,
}: {
  caregivers: RosterCaregiver[];
  onOpen: (caregiver: RosterCaregiver) => void;
}) {
  return (
    <div className="rounded-xl border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Caregiver</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Hours / available</TableHead>
            <TableHead>Utilization</TableHead>
            <TableHead>Credentials</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {caregivers.map((c) => {
            const status = resourceStatusMeta(c.status);
            const inactive = c.status === "inactive";
            return (
              <TableRow
                key={c.id}
                onClick={() => onOpen(c)}
                className={cn("cursor-pointer", inactive && "opacity-60")}
              >
                <TableCell>
                  <div className="font-medium">{c.name}</div>
                  {c.phone && (
                    <div className="text-xs text-muted-foreground">{c.phone}</div>
                  )}
                </TableCell>
                <TableCell>
                  <Badge variant={status.badge}>{status.label}</Badge>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  <span>{fmtHours(c.hours_this_week)}</span>
                  <span className="text-muted-foreground">
                    {" / "}
                    {fmtHours(c.available_hours)}
                  </span>
                </TableCell>
                <TableCell>
                  <UtilizationBar pct={c.utilization} />
                </TableCell>
                <TableCell>
                  <CredentialBadges credentials={c.credentials} max={4} />
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
