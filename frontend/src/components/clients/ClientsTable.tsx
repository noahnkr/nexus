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
import { fmtHours, payerLabel, statusMeta } from "@/lib/clients";
import type { Client } from "@/lib/api";

// The client directory table: name, status pill, payer, region, authorized
// hrs/wk, contact. Row click opens the care overview. A read surface with
// drill-in, mirroring the Leads/Caregivers directory pattern.
//
// (Per-client "scheduled this week" is deliberately absent — the list endpoint
// returns the client record only; hours live in the census strip and the
// profile's hours card, which is where the seam's per-week SQL runs. Adding a
// column here would mean a per-row hours query, which 16b's no-backend-work
// scope rules out.)
export function ClientsTable({ clients }: { clients: Client[] }) {
  const navigate = useNavigate();
  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Payer</TableHead>
            <TableHead>Region</TableHead>
            <TableHead className="text-right">Authorized/wk</TableHead>
            <TableHead>Contact</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {clients.map((client) => {
            const meta = statusMeta(client.status);
            return (
              <TableRow
                key={client.id}
                className="cursor-pointer"
                onClick={() => navigate(`/clients/${client.id}`)}
              >
                <TableCell className="font-medium">{client.name}</TableCell>
                <TableCell>
                  <Badge variant={meta.badge}>{meta.label}</Badge>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {payerLabel(client.payer)}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {client.region_name ?? "—"}
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {client.authorized_hours_per_week != null
                    ? fmtHours(client.authorized_hours_per_week)
                    : "—"}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  <span className="font-mono text-xs">
                    {client.phone ?? client.email ?? "—"}
                  </span>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
