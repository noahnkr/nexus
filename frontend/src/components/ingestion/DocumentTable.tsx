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

// Rows open the document drawer (M15b), where chunks, status detail, and a
// confirmed Delete live. Delete is deliberately no longer a one-click column
// here — removing a document also removes everything indexed from it.
export function DocumentTable({
  documents,
  onOpen,
}: {
  documents: DocumentOut[];
  onOpen: (doc: DocumentOut) => void;
}) {
  if (documents.length === 0) {
    return (
      <div className="rounded-lg border p-8 text-center text-sm text-muted-foreground">
        No documents yet. Upload one above to get started.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Filename</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Uploaded</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {documents.map((doc) => (
            <TableRow
              key={doc.id}
              role="button"
              tabIndex={0}
              onClick={() => onOpen(doc)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onOpen(doc);
                }
              }}
              className="cursor-pointer"
            >
              <TableCell className="font-medium">
                {doc.filename}
                {doc.status === "failed" && doc.error && (
                  <div className="mt-1 text-xs text-destructive">{doc.error}</div>
                )}
              </TableCell>
              <TableCell>
                <StatusBadge status={doc.status} />
              </TableCell>
              <TableCell className="whitespace-nowrap text-muted-foreground">
                {fmt(doc.created_at)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
