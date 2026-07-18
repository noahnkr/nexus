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
import { stageLabel, stageTone } from "@/lib/caregivers";
import type { Applicant } from "@/lib/api";

// The directory table: name, stage, source, qualifications count, age, contact. Row
// click opens the profile. Stage moves happen in the profile (no inline editing
// here) — a read surface with drill-in, mirroring LeadsTable.
export function ApplicantsTable({ applicants }: { applicants: Applicant[] }) {
  const navigate = useNavigate();
  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Stage</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Qualifications</TableHead>
            <TableHead>Age</TableHead>
            <TableHead>Contact</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {applicants.map((a) => (
            <TableRow
              key={a.id}
              className="cursor-pointer"
              onClick={() => navigate(`/caregivers/${a.id}`)}
            >
              <TableCell className="font-medium">{a.name}</TableCell>
              <TableCell>
                <Badge variant={stageTone(a.stage)}>{stageLabel(a.stage)}</Badge>
              </TableCell>
              <TableCell className="text-muted-foreground">
                {a.source ?? "—"}
              </TableCell>
              <TableCell className="text-muted-foreground">
                {a.qualification_names.length > 0
                  ? a.qualification_names.join(", ")
                  : "—"}
              </TableCell>
              <TableCell className="text-muted-foreground">
                {relativeTime(a.created_at)}
              </TableCell>
              <TableCell className="text-muted-foreground">
                <span className="font-mono text-xs">
                  {a.phone ?? a.email ?? "—"}
                </span>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
