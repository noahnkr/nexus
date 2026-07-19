import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Trash2, X } from "lucide-react";
import { api, type DocumentDetail, type DocumentOut } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ingestion/StatusBadge";
import { ConfirmDialog } from "@/components/automations/ConfirmDialog";

// Right-side sheet for one document (VisitDrawer / TaskDrawer pattern). The
// detail endpoint has always returned chunk previews — this is the first surface
// that shows them, which is what makes "is this actually in the knowledge base?"
// answerable without reading the database. Delete lives here behind a confirm
// rather than as a one-click column in the table.
function fmt(ts: string): string {
  return new Date(ts).toLocaleString();
}

export function DocumentDrawer({
  document,
  onClose,
  onDeleted,
}: {
  document: DocumentOut;
  onClose: () => void;
  onDeleted: (id: string) => void;
}) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [confirming, setConfirming] = useState(false);

  // Refetch when the row changes, and when its status does — a document that was
  // still processing when opened has no chunks yet, and Realtime will flip it.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getDocument(document.id)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => !cancelled && setDetail(null))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [document.id, document.status]);

  const doDelete = async () => {
    try {
      await api.deleteDocument(document.id);
      onDeleted(document.id);
      setConfirming(false);
      onClose();
      toast.success("Document deleted");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const chunks = detail?.chunks ?? [];

  return (
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="absolute right-0 top-0 flex h-full w-full flex-col border-l bg-card shadow-xl sm:max-w-md">
        <div className="flex items-start justify-between gap-3 border-b p-4">
          <div className="min-w-0">
            <h2 className="break-words text-base font-semibold">{document.filename}</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {document.mime_type || "Unknown type"}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <StatusBadge status={document.status} />
            <button
              onClick={onClose}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
          {document.status === "failed" && document.error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
              {document.error}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3 text-sm">
            <Field label="Uploaded">{fmt(document.created_at)}</Field>
            <Field label="Updated">{fmt(document.updated_at)}</Field>
            <Field label="Chunks">
              {loading ? "…" : (detail?.chunk_count ?? 0).toLocaleString()}
            </Field>
            <Field label="Status">{document.status}</Field>
          </div>

          <div>
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Content preview
            </div>
            {loading ? (
              <div className="space-y-2">
                <Skeleton className="h-16 w-full" />
                <Skeleton className="h-16 w-full" />
              </div>
            ) : chunks.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {document.status === "ready"
                  ? "No chunks were extracted from this document."
                  : "Chunks appear here once processing finishes."}
              </p>
            ) : (
              <div className="space-y-2">
                {chunks.map((c) => (
                  <div key={c.chunk_index} className="rounded-md border bg-muted/30 p-3">
                    <div className="mb-1 text-[11px] font-medium text-muted-foreground">
                      Chunk {c.chunk_index + 1}
                    </div>
                    <p className="whitespace-pre-wrap break-words text-xs leading-relaxed">
                      {c.chunk_text}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="border-t p-4">
          <Button
            variant="ghost"
            className="text-destructive hover:text-destructive"
            onClick={() => setConfirming(true)}
          >
            <Trash2 className="h-4 w-4" /> Delete document
          </Button>
        </div>
      </div>

      <ConfirmDialog
        open={confirming}
        title="Delete this document?"
        body={`"${document.filename}" and everything indexed from it will be removed from the knowledge base. This can't be undone.`}
        confirmLabel="Delete"
        destructive
        onConfirm={doDelete}
        onClose={() => setConfirming(false)}
      />
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-0.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="break-words">{children}</div>
    </div>
  );
}
