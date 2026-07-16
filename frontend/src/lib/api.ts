// Typed client for the FastAPI backend. All calls go through the Vite /api proxy
// in dev; in production the same paths are served behind the app origin.

export interface DocumentOut {
  id: string;
  filename: string;
  mime_type: string | null;
  status: "uploaded" | "processing" | "ready" | "failed";
  error: string | null;
  storage_path: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChunkPreview {
  chunk_index: number;
  chunk_text: string;
  metadata: Record<string, unknown>;
}

export interface DocumentDetail extends DocumentOut {
  chunk_count: number;
  chunks: ChunkPreview[];
}

export interface ThreadOut {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface Source {
  n: number;
  document_id: string;
  filename: string;
  chunk_id: string;
  chunk_index: number;
  snippet: string;
}

export interface ContentBlock {
  type: string;
  text?: string;
  [k: string]: unknown;
}

export interface MessageOut {
  id: string;
  role: "user" | "assistant";
  content: ContentBlock[];
  citations: Source[];
  metadata: Record<string, unknown>;
  created_at: string;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  // Documents
  listDocuments: () => fetch("/api/documents").then(json<DocumentOut[]>),
  getDocument: (id: string) =>
    fetch(`/api/documents/${id}`).then(json<DocumentDetail>),
  uploadDocument: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return fetch("/api/documents", { method: "POST", body }).then(json<DocumentOut>);
  },
  deleteDocument: (id: string) =>
    fetch(`/api/documents/${id}`, { method: "DELETE" }).then((r) => {
      if (!r.ok && r.status !== 204) throw new Error(`delete failed: ${r.status}`);
    }),

  // Chat threads
  listThreads: () => fetch("/api/chat/threads").then(json<ThreadOut[]>),
  createThread: (title?: string) =>
    fetch("/api/chat/threads", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title: title ?? null }),
    }).then(json<ThreadOut>),
  deleteThread: (id: string) =>
    fetch(`/api/chat/threads/${id}`, { method: "DELETE" }).then((r) => {
      if (!r.ok && r.status !== 204) throw new Error(`delete failed: ${r.status}`);
    }),
  listMessages: (threadId: string) =>
    fetch(`/api/chat/threads/${threadId}/messages`).then(json<MessageOut[]>),

  // Realtime token (dev seam; replaced by Supabase Auth in Module 6)
  getRealtimeToken: () =>
    fetch("/api/auth/realtime-token").then(json<{ token: string; expires_in: number }>),
};
