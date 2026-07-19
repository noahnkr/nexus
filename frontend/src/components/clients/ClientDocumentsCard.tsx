import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { FileText, Trash2, UploadCloud } from "lucide-react";
import { api, type DocumentOut } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/automations/ConfirmDialog";
import { relativeTime } from "@/lib/utils";

// Care plans & records for one client: the documents tagged to this client, with a
// preset upload (the entity tag is applied invisibly — the user just picks a file)
// and a confirmed delete. Live status via Realtime on the documents table, filtered
// to this client's id. The Ingestion/Knowledge page is untouched — this is the one
// place the tag is set from the UI.
type DocStatus = DocumentOut["status"];

const STATUS_META: Record<DocStatus, { label: string; variant: "info" | "success" | "destructive" | "secondary" }> = {
  uploaded: { label: "Uploaded", variant: "secondary" },
  processing: { label: "Processing", variant: "info" },
  ready: { label: "Ready", variant: "success" },
  failed: { label: "Failed", variant: "destructive" },
};

export function ClientDocumentsCard({ clientId }: { clientId: string }) {
  const [docs, setDocs] = useState<DocumentOut[]>([]);
  const [uploading, setUploading] = useState(false);
  const [deleting, setDeleting] = useState<DocumentOut | null>(null);

  const tag = { entity_type: "client", entity_id: clientId };

  const upsert = useCallback(
    (doc: DocumentOut) => {
      // Realtime carries every tenant document; keep only this client's.
      if (doc.entity_type !== "client" || doc.entity_id !== clientId) return;
      setDocs((prev) => {
        const idx = prev.findIndex((d) => d.id === doc.id);
        if (idx === -1) return [doc, ...prev];
        const next = [...prev];
        next[idx] = { ...next[idx], ...doc };
        return next;
      });
    },
    [clientId],
  );

  const load = useCallback(() => {
    api.listDocuments(tag).then(setDocs).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const channel = supabase
      .channel(`client-docs-${clientId}`)
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "documents" },
        (p) => upsert(p.new as DocumentOut),
      )
      .on(
        "postgres_changes",
        { event: "UPDATE", schema: "public", table: "documents" },
        (p) => upsert(p.new as DocumentOut),
      )
      .subscribe();
    return () => {
      supabase.removeChannel(channel);
    };
  }, [clientId, upsert]);

  const onFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    for (const file of Array.from(files)) {
      try {
        const doc = await api.uploadDocument(file, tag);
        upsert(doc);
        toast.success(`Uploaded ${file.name}`);
      } catch (e) {
        toast.error(`Upload failed: ${file.name}`);
        console.error(e);
      }
    }
    setUploading(false);
  };

  const onDelete = async () => {
    if (!deleting) return;
    try {
      await api.deleteDocument(deleting.id);
      setDocs((prev) => prev.filter((d) => d.id !== deleting.id));
      setDeleting(null);
      toast.success("Document removed");
    } catch (e) {
      toast.error(String(e));
    }
  };

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Care plans & records
          </p>
          <label className="inline-flex cursor-pointer items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            <UploadCloud className="h-3.5 w-3.5" />
            {uploading ? "Uploading…" : "Upload"}
            <input
              type="file"
              multiple
              accept=".pdf,.docx,.html,.htm,.md,.markdown,.txt"
              className="hidden"
              disabled={uploading}
              onChange={(e) => {
                onFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </label>
        </div>

        {docs.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No documents yet. Upload a care plan or assessment to attach it here.
          </p>
        ) : (
          <ul className="divide-y">
            {docs.map((d) => {
              const meta = STATUS_META[d.status];
              return (
                <li key={d.id} className="flex items-center justify-between gap-3 py-2.5 first:pt-0">
                  <div className="flex min-w-0 items-center gap-2">
                    <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{d.filename}</p>
                      <p className="text-xs text-muted-foreground">{relativeTime(d.created_at)}</p>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge variant={meta.variant}>{meta.label}</Badge>
                    <button
                      onClick={() => setDeleting(d)}
                      className="text-muted-foreground hover:text-destructive"
                      aria-label={`Remove ${d.filename}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        <p className="text-xs text-muted-foreground">
          Documents here are searchable in chat and tied to this client.
        </p>
      </CardContent>

      <ConfirmDialog
        open={deleting !== null}
        title="Remove this document?"
        body={`This deletes ${deleting?.filename ?? "the document"} and removes it from search. This can't be undone.`}
        confirmLabel="Remove"
        destructive
        onConfirm={onDelete}
        onClose={() => setDeleting(null)}
      />
    </Card>
  );
}
