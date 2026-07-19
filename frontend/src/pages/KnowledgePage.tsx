import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { api, type DocumentOut } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/layout/PageHeader";
import { DocumentTable } from "@/components/ingestion/DocumentTable";
import { UploadDropzone } from "@/components/ingestion/UploadDropzone";
import { DocumentDrawer } from "@/components/knowledge/DocumentDrawer";
import { InstructionsTab } from "@/components/knowledge/InstructionsTab";

// Knowledge = what the assistant knows (Documents) plus how it behaves
// (Instructions). Replaces the old Ingestion page; /ingestion redirects here.
// The active tab lives in the URL so it can be linked to (Settings points at
// ?tab=instructions) and survives a reload.
type Tab = "documents" | "instructions";

const TABS: { id: Tab; label: string }[] = [
  { id: "documents", label: "Documents" },
  { id: "instructions", label: "Instructions" },
];

export function KnowledgePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: Tab = searchParams.get("tab") === "instructions" ? "instructions" : "documents";

  const setTab = (next: Tab) => {
    const params = new URLSearchParams(searchParams);
    if (next === "documents") params.delete("tab");
    else params.set("tab", next);
    setSearchParams(params, { replace: true });
  };

  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [uploading, setUploading] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);

  const upsert = useCallback((doc: DocumentOut) => {
    setDocuments((prev) => {
      const idx = prev.findIndex((d) => d.id === doc.id);
      if (idx === -1) return [doc, ...prev];
      const next = [...prev];
      next[idx] = { ...next[idx], ...doc };
      return next;
    });
  }, []);

  useEffect(() => {
    api.listDocuments().then(setDocuments).catch((e) => toast.error(String(e)));
  }, []);

  // Live status via Supabase Realtime. The signed-in supabase-js client forwards
  // the session token to Realtime automatically, so postgres_changes RLS scopes
  // to the tenant with no extra wiring.
  useEffect(() => {
    const channel = supabase
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
    return () => {
      supabase.removeChannel(channel);
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

  const openDocument = documents.find((d) => d.id === openId) ?? null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Knowledge"
        description="What the assistant knows, and how it should behave."
      />

      <div className="border-b px-4 sm:px-6">
        <div className="flex gap-1 overflow-x-auto">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                "-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition-colors",
                tab === t.id
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
        {tab === "documents" ? (
          <div className="flex flex-col gap-6">
            <UploadDropzone onFiles={handleFiles} disabled={uploading} />
            <DocumentTable documents={documents} onOpen={(d) => setOpenId(d.id)} />
          </div>
        ) : (
          <InstructionsTab />
        )}
      </div>

      {openDocument && (
        <DocumentDrawer
          document={openDocument}
          onClose={() => setOpenId(null)}
          onDeleted={(id) => setDocuments((prev) => prev.filter((d) => d.id !== id))}
        />
      )}
    </div>
  );
}
