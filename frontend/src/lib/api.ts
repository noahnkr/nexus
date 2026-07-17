// Typed client for the FastAPI backend. All calls go through the Vite /api proxy
// in dev; in production the same paths are served behind the app origin.
import { supabase } from "./supabase";

// Every /api request carries the signed-in session's access token. A 401 means
// the session expired or was revoked: sign out so the AuthProvider bounces to
// /login. This is the single place a bearer header is attached — no scattered
// literals, and FormData uploads keep their multipart content-type untouched.
export async function authFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(input, { ...init, headers });
  if (res.status === 401) {
    await supabase.auth.signOut();
  }
  return res;
}

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

// One tool call the agent made this turn, stored on the final assistant message's
// metadata so tool activity can be rendered on history reload.
export interface ToolCall {
  name: string;
  summary: string;
  is_error: boolean;
  queued?: boolean;
}

export interface MessageOut {
  id: string;
  role: "user" | "assistant";
  content: ContentBlock[];
  citations: Source[];
  metadata: Record<string, unknown>;
  created_at: string;
}

// --- Event Log ---------------------------------------------------------------
export interface EventOut {
  id: string;
  created_at: string;
  source_system: string;
  event_type: string;
  entity_type: string | null;
  entity_id: string | null;
  summary: string;
  payload: Record<string, unknown>;
}

export interface EventPage {
  events: EventOut[];
  next_cursor: string | null;
}

export interface EventFacets {
  source_systems: string[];
  event_types: string[];
}

export interface EventQuery {
  source_system?: string;
  event_type?: string;
  entity_type?: string;
  entity_id?: string;
  since?: string;
  until?: string;
  cursor?: string;
  limit?: number;
}

// --- Tasks & approvals -------------------------------------------------------
export type TaskStatus = "pending" | "in_progress" | "done" | "cancelled";
export type TaskPriority = "low" | "normal" | "high" | "urgent";
export type ActionStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "executed"
  | "failed";

export interface ActionResult {
  summary?: string;
  error?: string;
}

export interface PendingAction {
  id: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
  status: ActionStatus;
  source_system: string;
  result: ActionResult | null;
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
}

export interface Task {
  id: string;
  title: string;
  description: string | null;
  status: TaskStatus;
  priority: TaskPriority;
  originating_event_id: string | null;
  assigned_to: string | null;
  due_at: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
  pending_actions: PendingAction[];
}

export interface TaskPage {
  tasks: Task[];
  next_cursor: string | null;
}

export interface TaskQuery {
  status?: string; // comma-separated set, e.g. "pending,in_progress"
  priority?: string;
  cursor?: string;
  limit?: number;
}

export interface TaskCreate {
  title: string;
  description?: string;
  priority?: TaskPriority;
  due_at?: string | null;
}

export interface ActionResolution {
  action: PendingAction;
  task: Task;
}

// --- Home summary ------------------------------------------------------------
export interface HomeSummary {
  open_tasks: number;
  pending_approvals: number;
  documents: { ready: number; processing: number; failed: number };
  events_today: number;
}

function queryString(params: Record<string, unknown>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

function eventQueryString(params: EventQuery): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  // Home
  getHomeSummary: () => authFetch("/api/home/summary").then(json<HomeSummary>),

  // Documents
  listDocuments: () => authFetch("/api/documents").then(json<DocumentOut[]>),
  getDocument: (id: string) =>
    authFetch(`/api/documents/${id}`).then(json<DocumentDetail>),
  uploadDocument: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return authFetch("/api/documents", { method: "POST", body }).then(json<DocumentOut>);
  },
  deleteDocument: (id: string) =>
    authFetch(`/api/documents/${id}`, { method: "DELETE" }).then((r) => {
      if (!r.ok && r.status !== 204) throw new Error(`delete failed: ${r.status}`);
    }),

  // Chat threads
  listThreads: () => authFetch("/api/chat/threads").then(json<ThreadOut[]>),
  createThread: (title?: string) =>
    authFetch("/api/chat/threads", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title: title ?? null }),
    }).then(json<ThreadOut>),
  deleteThread: (id: string) =>
    authFetch(`/api/chat/threads/${id}`, { method: "DELETE" }).then((r) => {
      if (!r.ok && r.status !== 204) throw new Error(`delete failed: ${r.status}`);
    }),
  listMessages: (threadId: string) =>
    authFetch(`/api/chat/threads/${threadId}/messages`).then(json<MessageOut[]>),

  // Event Log
  listEvents: (params: EventQuery = {}) =>
    authFetch(`/api/events${eventQueryString(params)}`).then(json<EventPage>),
  getEventFacets: () => authFetch("/api/events/facets").then(json<EventFacets>),

  // Tasks & approvals
  listTasks: (params: TaskQuery = {}) =>
    authFetch(`/api/tasks${queryString(params as Record<string, unknown>)}`).then(
      json<TaskPage>,
    ),
  createTask: (body: TaskCreate) =>
    authFetch("/api/tasks", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<Task>),
  patchTask: (id: string, status: TaskStatus) =>
    authFetch(`/api/tasks/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ status }),
    }).then(json<Task>),
  approveAction: (id: string) =>
    authFetch(`/api/pending-actions/${id}/approve`, { method: "POST" }).then(
      json<ActionResolution>,
    ),
  rejectAction: (id: string, note?: string) =>
    authFetch(`/api/pending-actions/${id}/reject`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ note: note ?? null }),
    }).then(json<ActionResolution>),
};
