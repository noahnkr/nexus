import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { api, type DocumentOut } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { DocumentTable } from "@/components/ingestion/DocumentTable";
import { UploadDropzone } from "@/components/ingestion/UploadDropzone";

export function IngestionPage() {
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [uploading, setUploading] = useState(false);

  const upsert = useCallback((doc: DocumentOut) => {
    setDocuments((prev) => {
      const idx = prev.findIndex((d) => d.id === doc.id);
      if (idx === -1) return [doc, ...prev];
      const next = [...prev];
      next[idx] = { ...next[idx], ...doc };
      return next;
    });
  }, []);

  // Initial load.
  useEffect(() => {
    api.listDocuments().then(setDocuments).catch((e) => toast.error(String(e)));
  }, []);

  // Live status via Supabase Realtime. The anon client needs the tenant token.
  useEffect(() => {
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;
    (async () => {
      try {
        const { token } = await api.getRealtimeToken();
        await supabase.realtime.setAuth(token);
      } catch {
        // Realtime is a live-updates convenience; the table still loads without it.
      }
      if (cancelled) return;
      channel = supabase
        .channel("documents-changes")
        .on(
          "postgres_changes",
          { event: "INSERT", schema: "public", table: "documents" },
          (payload) => upsert(payload.new as DocumentOut),
        )
        .on(
          "postgres_changes",
          { event: "UPDATE", schema: "public", table: "documents" },
          (payload) => upsert(payload.new as DocumentOut),
        )
        .subscribe();
    })();
    return () => {
      cancelled = true;
      if (channel) supabase.removeChannel(channel);
    };
  }, [upsert]);

  const handleFiles = async (files: File[]) => {
    setUploading(true);
    for (const file of files) {
      try {
        const doc = await api.uploadDocument(file);
        upsert(doc);
        toast.success(`Uploaded ${file.name}`);
      } catch (e) {
        toast.error(`Upload failed: ${file.name}`);
        console.error(e);
      }
    }
    setUploading(false);
  };

  const handleDelete = async (id: string) => {
    try {
      await api.deleteDocument(id);
      setDocuments((prev) => prev.filter((d) => d.id !== id));
      toast.success("Document deleted");
    } catch (e) {
      toast.error(String(e));
    }
  };

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center border-b px-6">
        <h1 className="text-lg font-semibold">Ingestion</h1>
      </header>
      <div className="flex flex-col gap-6 p-6">
        <UploadDropzone onFiles={handleFiles} disabled={uploading} />
        <DocumentTable documents={documents} onDelete={handleDelete} />
      </div>
    </div>
  );
}
