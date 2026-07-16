import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { DocumentOut } from "@/lib/api";
import { StatusBadge } from "./StatusBadge";

function fmt(ts: string) {
  return new Date(ts).toLocaleString();
}

export function DocumentTable({
  documents,
  onDelete,
}: {
  documents: DocumentOut[];
  onDelete: (id: string) => void;
}) {
  if (documents.length === 0) {
    return (
      <div className="rounded-lg border p-8 text-center text-sm text-muted-foreground">
        No documents yet. Upload one above to get started.
      </div>
    );
  }
  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Filename</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Uploaded</TableHead>
            <TableHead className="w-10" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {documents.map((doc) => (
            <TableRow key={doc.id}>
              <TableCell className="font-medium">
                {doc.filename}
                {doc.status === "failed" && doc.error && (
                  <div className="mt-1 text-xs text-destructive">{doc.error}</div>
                )}
              </TableCell>
              <TableCell>
                <StatusBadge status={doc.status} />
              </TableCell>
              <TableCell className="text-muted-foreground">
                {fmt(doc.created_at)}
              </TableCell>
              <TableCell>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => onDelete(doc.id)}
                  aria-label={`Delete ${doc.filename}`}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
