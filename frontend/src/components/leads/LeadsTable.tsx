import { useNavigate } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { relativeTime } from "@/lib/utils";
import { stageLabel, stageTone } from "@/lib/leads";
import type { Lead } from "@/lib/api";

// The directory table: name, stage, source, region, age, contact. Row click opens
// the profile. Stage moves happen in the profile (no inline editing here) — this
// is a read surface with drill-in, mirroring the Tasks/Events list pattern.
export function LeadsTable({ leads }: { leads: Lead[] }) {
  const navigate = useNavigate();
  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Stage</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Region</TableHead>
            <TableHead>Age</TableHead>
            <TableHead>Contact</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {leads.map((lead) => (
            <TableRow
              key={lead.id}
              className="cursor-pointer"
              onClick={() => navigate(`/leads/${lead.id}`)}
            >
              <TableCell className="font-medium">{lead.name}</TableCell>
              <TableCell>
                <Badge variant={stageTone(lead.status)}>
                  {stageLabel(lead.status)}
                </Badge>
              </TableCell>
              <TableCell className="text-muted-foreground">
                {lead.source ?? "—"}
              </TableCell>
              <TableCell className="text-muted-foreground">
                {lead.region_name ?? "—"}
              </TableCell>
              <TableCell className="text-muted-foreground">
                {relativeTime(lead.created_at)}
              </TableCell>
              <TableCell className="text-muted-foreground">
                <span className="font-mono text-xs">
                  {lead.phone ?? lead.email ?? "—"}
                </span>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
