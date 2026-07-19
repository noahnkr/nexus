// Typed client for the FastAPI backend. All calls go through the Vite /api proxy
// in dev; in production the same paths are served behind the app origin.
import { supabase } from "./supabase";
import type { Condition, RunStatus, Step, Trigger } from "./recipe";

export type { RunStatus };

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
  // Optional canonical-entity tag (M16a) — a care plan is a document tagged to a
  // client. Null on tenant-general uploads.
  entity_type: string | null;
  entity_id: string | null;
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
  edited?: boolean;
  edited_fields?: string[];
}

export interface PendingAction {
  id: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
  status: ActionStatus;
  source_system: string;
  // Input keys the approver may reword before approving (resolved server-side
  // from the tool registry). Empty means approve-verbatim-or-reject.
  editable_fields: string[];
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

// --- Settings ----------------------------------------------------------------
export type AgentTone = "balanced" | "professional" | "friendly" | "concise";

// Per-tenant, user-facing preferences. Infra config and credentials are env-only
// and never appear here.
export interface TenantSettings {
  workspace_name: string;
  agent_instructions: string;
  agent_tone: AgentTone;
}

// --- Automations -------------------------------------------------------------
export interface LastRun {
  status: RunStatus;
  at: string;
}

export interface Automation {
  id: string;
  name: string;
  description: string | null;
  status: "active" | "paused";
  trigger: Trigger;
  conditions: Condition[];
  steps: Step[];
  next_fire_at: string | null;
  created_by: string | null;
  active_runs: number;
  last_run: LastRun | null;
  requires_approval: boolean;
  binding: Record<string, unknown> | null; // generic view-binding (9b), null = unbound
  created_at: string;
  updated_at: string;
}

export interface AutomationCreate {
  name: string;
  description?: string | null;
  trigger: Trigger;
  conditions?: Condition[];
  steps?: Step[];
  binding?: Record<string, unknown> | null;
}

export interface AutomationPatch {
  name?: string;
  description?: string | null;
  status?: "active" | "paused";
  trigger?: Trigger;
  conditions?: Condition[];
  steps?: Step[];
  binding?: Record<string, unknown> | null;
}

export interface StepLogEntry {
  index: number;
  type: string;
  summary: string;
  status: "ok" | "queued" | "waiting" | "stopped" | "failed";
  at: string;
}

export interface Run {
  id: string;
  automation_id: string;
  status: RunStatus;
  trigger_event_id: string | null;
  entity_type: string | null;
  entity_id: string | null;
  context: Record<string, unknown>;
  step_index: number;
  step_log: StepLogEntry[];
  wake_at: string | null;
  error: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

// --- Builder vocabulary + drafting (8b) --------------------------------------
export interface VocabTool {
  name: string;
  label: string;
  description: string;
  input_schema: JSONSchema;
  safe: boolean;
}

export interface VocabFunction {
  name: string;
  description: string;
  input_schema: JSONSchema;
}

// Trigger-aware, plain-language field knowledge (Module 11a) — the token picker
// and label helpers render from this. Mirrors backend schemas.FieldCatalog.
export interface FieldRef {
  path: string;
  label: string;
}

export interface EntityFields {
  label: string;
  fields: FieldRef[];
}

export interface FieldCatalog {
  trigger_fields: FieldRef[];
  payload_by_event: Record<string, FieldRef[]>;
  entities: Record<string, EntityFields>;
  event_entity: Record<string, string>;
}

export interface Vocabulary {
  triggers: { event_types: string[]; source_systems: string[] };
  tools: VocabTool[];
  functions: VocabFunction[];
  operators: string[];
  generate_models: string[];
  field_roots: string[];
  field_suggestions: string[];
  // Optional for resilience against older cached responses; present from 11a on.
  field_catalog?: FieldCatalog;
}

export interface JSONSchema {
  type?: string;
  properties?: Record<string, JSONSchemaProp>;
  required?: string[];
}

export interface JSONSchemaProp {
  type?: string;
  description?: string;
  enum?: string[];
  default?: unknown;
  maximum?: number;
}

export interface AutomationDraft {
  name: string;
  description: string | null;
  trigger: Trigger;
  conditions: Condition[];
  steps: Step[];
  explanation: string;
}

// --- Leads view (Module 9, vertical seam) ------------------------------------
export type LeadStatus =
  | "new"
  | "contacted"
  | "qualified"
  | "converted"
  | "lost";

export interface RegionRef {
  id: string;
  name: string;
}

export interface Lead {
  id: string;
  name: string;
  phone: string | null;
  email: string | null;
  source: string | null;
  status: LeadStatus;
  region_id: string | null;
  region_name: string | null;
  requirements: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface LeadPage {
  leads: Lead[];
  total: number;
}

export interface LeadFacets {
  sources: string[];
  regions: RegionRef[];
}

export interface LeadSummary {
  summary: string;
  generated_at: string;
}

export interface StageCount {
  stage: string;
  count: number;
}

export interface SourceCount {
  source: string;
  count: number;
}

export interface LeadMetrics {
  stages: StageCount[];
  conversion_rate: number;
  new_last_7_days: number;
  avg_days_to_convert: number | null;
  top_sources: SourceCount[];
}

export interface LeadCreate {
  name: string;
  phone?: string | null;
  email?: string | null;
  source?: string | null;
  region_id?: string | null;
}

// Only the fields being changed are sent (the server writes just those and emits
// the matching event). region_id may be null to clear the region.
export interface LeadPatch {
  name?: string;
  phone?: string | null;
  email?: string | null;
  source?: string | null;
  region_id?: string | null;
  status?: LeadStatus;
}

export interface LeadQuery {
  status?: string;
  source?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

// --- Caregivers view (Module 10, vertical seam) ------------------------------
export type ApplicantStage =
  | "applied"
  | "screening"
  | "interview"
  | "offer"
  | "hired"
  | "rejected";

export interface QualificationRef {
  id: string;
  name: string;
}

export interface Applicant {
  id: string;
  name: string;
  phone: string | null;
  email: string | null;
  source: string | null;
  stage: ApplicantStage;
  qualification_ids: string[];
  region_ids: string[];
  qualification_names: string[];
  region_names: string[];
  availability: Record<string, unknown>;
  notes: string | null;
  created_at: string;
  updated_at: string;
  // Set only on the PATCH response that moved an applicant to `hired` — the
  // profile's hired banner names the created caregiver. Null on every read.
  promoted_resource_id: string | null;
  promoted_resource_name: string | null;
}

export interface ApplicantPage {
  applicants: Applicant[];
  total: number;
}

export interface ApplicantFacets {
  sources: string[];
  regions: RegionRef[];
  qualifications: QualificationRef[];
}

export interface ApplicantSummary {
  summary: string;
  generated_at: string;
}

export interface ApplicantMetrics {
  stages: StageCount[];
  hire_rate: number;
  new_last_7_days: number;
  avg_days_to_hire: number | null;
  top_sources: SourceCount[];
}

export interface ApplicantCreate {
  name: string;
  phone?: string | null;
  email?: string | null;
  source?: string | null;
  qualification_ids?: string[];
  region_ids?: string[];
}

// Only the fields being changed are sent (the server writes just those and emits
// the matching event). A `stage` change routes through move_stage().
export interface ApplicantPatch {
  name?: string;
  phone?: string | null;
  email?: string | null;
  source?: string | null;
  notes?: string | null;
  qualification_ids?: string[];
  region_ids?: string[];
  stage?: ApplicantStage;
}

export interface ApplicantQuery {
  stage?: string;
  source?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

// --- Schedule board (Module 12, vertical seam) -------------------------------
export type VisitStatus =
  | "open"
  | "scheduled"
  | "called_out"
  | "completed"
  | "cancelled"
  | "no_show";

export interface ScheduleVisit {
  id: string;
  client_id: string;
  client_name: string;
  resource_id: string | null; // null for an unfilled 'open' shift
  resource_name: string | null;
  start_time: string;
  end_time: string;
  status: VisitStatus;
  required_qualification_ids: string[];
  required_qualification_names: string[];
  replaces_schedule_id: string | null;
  notes: string | null;
  // EVV (M16a): raw clock stamps + the server-computed read-time flag. `evv` is
  // 'late' | 'missed' | null, derived per request — never stored — so a badge can't
  // outlive the caregiver finally clocking in.
  check_in_at: string | null;
  check_out_at: string | null;
  evv: "late" | "missed" | null;
  created_at: string;
  updated_at: string;
}

// A weekday -> time-range list, e.g. { mon: ["08:00-16:00"] }.
export type Availability = Record<string, string[]>;

export interface CaregiverRoster {
  id: string;
  name: string;
  phone: string | null;
  email: string | null;
  address: string | null;
  zip: string | null;
  languages: string[];
  traits: string[];
  qualification_ids: string[];
  region_ids: string[];
  availability: Availability;
  hours_this_week: number;
}

export interface ClientRef {
  id: string;
  name: string;
}

export interface ScheduleBoard {
  week_start: string; // Monday of the requested week (YYYY-MM-DD)
  visits: ScheduleVisit[];
  caregivers: CaregiverRoster[];
  clients: ClientRef[]; // for the create dialog's client picker
}

export interface ScheduleCreate {
  client_id: string;
  resource_id?: string | null; // omit for an open shift
  start_time: string;
  end_time: string;
  required_qualification_ids?: string[];
  notes?: string | null;
  repeat_weekly_until?: string | null; // YYYY-MM-DD
}

export interface SchedulesCreated {
  visits: ScheduleVisit[]; // every row created (a weekly series expands)
}

export interface SchedulePatch {
  start_time?: string;
  end_time?: string;
  notes?: string | null;
  required_qualification_ids?: string[];
  status?: "completed" | "no_show"; // outcome only; transitions have their own verbs
}

export interface AssignResult {
  schedule_id: string;
  resource_id: string;
  status: string;
  warnings: string[]; // qualification/availability gaps (non-blocking)
}

export interface CallOutResult {
  schedule_id: string;
  replacement_schedule_id: string;
}

export interface Candidate {
  resource_id: string;
  name: string;
  phone: string | null;
  score: number;
  reasons: string[];
  warnings: string[];
}

export interface CandidatesOut {
  candidates: Candidate[];
}

export interface RosterPatch {
  name?: string;
  phone?: string | null;
  email?: string | null;
  address?: string | null;
  zip?: string | null;
  languages?: string[];
  traits?: string[];
  availability?: Availability;
}

export interface NotifyResult {
  status: string; // "queued"
  task_id: string | null;
  pending_action_id: string | null;
  summary: string;
}

// --- Clients view (Module 16, vertical seam) ---------------------------------
export type ClientStatus = "active" | "hospital_hold" | "discharged";
export type Payer = "private_pay" | "medicaid" | "ltc_insurance" | "va" | "other";

export interface Client {
  id: string;
  name: string;
  phone: string | null;
  email: string | null;
  status: ClientStatus;
  lead_id: string | null;
  address: string | null;
  zip: string | null;
  languages: string[];
  preferences: string[];
  region_id: string | null;
  region_name: string | null;
  payer: Payer | null; // null = unknown (intake in progress)
  authorized_hours_per_week: number | null;
  care_summary: string | null;
  requirements: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ClientPage {
  clients: Client[];
  total: number;
}

export interface ClientFacets {
  statuses: string[];
  payers: string[];
  regions: RegionRef[];
}

// One week of hours for a client or the whole census. `leakage_hours` (authorized
// minus delivered) is the number that matters.
export interface ClientHours {
  week_start: string;
  week_end: string;
  authorized_hours: number;
  scheduled_hours: number;
  delivered_hours: number;
  open_hours: number;
  leakage_hours: number;
  delivery_rate: number | null; // % of authorized; null when authorized = 0
}

export interface RegionCount {
  region_id: string | null;
  region: string;
  count: number;
}

export interface PayerCount {
  payer: string; // payer key, or 'unknown' for clients with none recorded
  count: number;
}

export interface CensusMetrics extends ClientHours {
  active_clients: number;
  by_region: RegionCount[];
  by_payer: PayerCount[];
}

export interface ClientContact {
  id: string;
  client_id: string;
  name: string;
  relationship: string | null;
  phone: string | null;
  email: string | null;
  is_primary: boolean;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface ClientContactCreate {
  name: string;
  relationship?: string | null;
  phone?: string | null;
  email?: string | null;
  is_primary?: boolean;
  notes?: string | null;
}

export interface ClientContactPatch {
  name?: string;
  relationship?: string | null;
  phone?: string | null;
  email?: string | null;
  is_primary?: boolean;
  notes?: string | null;
}

export interface ClientCaregiverRef {
  resource_id: string;
  name: string;
  next_visit: string | null;
}

export interface ClientDocumentRef {
  id: string;
  filename: string;
  status: string;
  created_at: string;
}

export interface ClientDetail extends Client {
  contacts: ClientContact[];
  caregivers: ClientCaregiverRef[];
  hours_this_week: ClientHours;
  documents: ClientDocumentRef[];
}

// The profile's visits card. Rows are the board's ScheduleVisit shape (same EVV
// flag + status meta), so the profile and the board render them identically.
export interface ClientVisits {
  upcoming: ScheduleVisit[];
  past: ScheduleVisit[];
}

export interface ClientSummary {
  summary: string;
  generated_at: string;
}

export interface ClientCreate {
  name: string;
  phone?: string | null;
  email?: string | null;
  address?: string | null;
  zip?: string | null;
  region_id?: string | null;
  payer?: Payer | null;
  authorized_hours_per_week?: number | null;
  care_summary?: string | null;
  languages?: string[];
  preferences?: string[];
}

// Only the fields being changed are sent. A `status` change routes through the
// server's change_status() path (emits client.status_changed).
export interface ClientPatch {
  name?: string;
  phone?: string | null;
  email?: string | null;
  address?: string | null;
  zip?: string | null;
  region_id?: string | null;
  payer?: Payer | null;
  authorized_hours_per_week?: number | null;
  care_summary?: string | null;
  languages?: string[];
  preferences?: string[];
  status?: ClientStatus;
}

export interface ClientQuery {
  status?: string;
  payer?: string;
  region_id?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

// --- Referrals dashboard (Module 17, vertical seam) --------------------------
export type PartnerCategory =
  | "hospital"
  | "senior_living"
  | "discharge_planner"
  | "home_health"
  | "community"
  | "other";

export interface Partner {
  id: string;
  name: string;
  category: PartnerCategory | null; // null = untyped
  contact_name: string | null;
  phone: string | null;
  email: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface PartnerCreate {
  name: string;
  category?: PartnerCategory | null;
  contact_name?: string | null;
  phone?: string | null;
  email?: string | null;
  notes?: string | null;
}

// Only the fields being changed are sent. A rename simply re-joins by the new name.
export interface PartnerPatch {
  name?: string;
  category?: PartnerCategory | null;
  contact_name?: string | null;
  phone?: string | null;
  email?: string | null;
  notes?: string | null;
}

// The partner enrichment attached to a source row (null when the source is untracked).
export interface ReferralPartnerRef {
  id: string;
  category: PartnerCategory | null;
  contact_name: string | null;
  phone: string | null;
  email: string | null;
  notes: string | null;
}

export interface MonthCount {
  month: string; // 'YYYY-MM' bucket key
  count: number;
}

// One distinct leads.source (or a tracked partner with no leads yet).
export interface ReferralSourceRow {
  source: string;
  partner: ReferralPartnerRef | null;
  leads_total: number;
  in_pipeline: number;
  converted: number;
  lost: number;
  conversion_rate: number; // converted / all leads for this source, %
  avg_days_to_convert: number | null;
  hours_won: number; // summed authorized hours/week of linked won clients
  last_lead_at: string | null;
  monthly: MonthCount[];
}

export interface BestConverter {
  source: string;
  conversion_rate: number;
}

export interface ReferralTotals {
  tracked_partners: number;
  leads_last_30_days: number;
  total_hours_won: number;
  best_converter: BestConverter | null; // null below the leads threshold
}

export interface ReferralMetrics {
  sources: ReferralSourceRow[];
  totals: ReferralTotals;
  months: string[]; // ordered 'YYYY-MM' window (oldest first)
  monthly: MonthCount[]; // ALL leads per month (the overall trend row)
}

// --- Home summary ------------------------------------------------------------
export interface HomeSummary {
  open_tasks: number;
  pending_approvals: number;
  open_shifts: number;
  documents: { ready: number; processing: number; failed: number };
  events_today: number;
  automations: { active: number; runs_today: number; failed_today: number };
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

  // Settings (per-tenant workspace + agent preferences)
  getSettings: () => authFetch("/api/settings").then(json<TenantSettings>),
  updateSettings: (patch: Partial<TenantSettings>) =>
    authFetch("/api/settings", {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(patch),
    }).then(json<TenantSettings>),

  // Documents
  listDocuments: (tag?: { entity_type: string; entity_id: string }) =>
    authFetch(`/api/documents${queryString(tag ?? {})}`).then(json<DocumentOut[]>),
  getDocument: (id: string) =>
    authFetch(`/api/documents/${id}`).then(json<DocumentDetail>),
  // An optional entity tag associates the upload with a canonical record (e.g. a
  // client's care plan); chunks inherit it. Omit for tenant-general knowledge.
  uploadDocument: (file: File, tag?: { entity_type: string; entity_id: string }) => {
    const body = new FormData();
    body.append("file", file);
    if (tag) {
      body.append("entity_type", tag.entity_type);
      body.append("entity_id", tag.entity_id);
    }
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
  // `edits` carries only the fields the approver changed; the server re-validates
  // them against the tool's editable_fields and 422s anything else.
  approveAction: (id: string, edits?: Record<string, string>) =>
    authFetch(`/api/pending-actions/${id}/approve`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ tool_input: edits ?? null }),
    }).then(json<ActionResolution>),
  rejectAction: (id: string, note?: string) =>
    authFetch(`/api/pending-actions/${id}/reject`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ note: note ?? null }),
    }).then(json<ActionResolution>),

  // Automations
  listAutomations: (opts?: { status?: "active" | "paused"; view?: string }) =>
    authFetch(
      `/api/automations${queryString({ status: opts?.status, view: opts?.view })}`,
    ).then(json<Automation[]>),
  getAutomation: (id: string) =>
    authFetch(`/api/automations/${id}`).then(json<Automation>),
  createAutomation: (body: AutomationCreate) =>
    authFetch("/api/automations", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<Automation>),
  patchAutomation: (id: string, body: AutomationPatch) =>
    authFetch(`/api/automations/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<Automation>),
  deleteAutomation: (id: string) =>
    authFetch(`/api/automations/${id}`, { method: "DELETE" }).then((r) => {
      if (!r.ok && r.status !== 204) throw new Error(`delete failed: ${r.status}`);
    }),
  runAutomation: (id: string, entity?: { entity_type?: string; entity_id?: string }) =>
    authFetch(`/api/automations/${id}/run`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(entity ?? {}),
    }).then(json<Run>),
  listRuns: (id: string, limit = 50) =>
    authFetch(`/api/automations/${id}/runs?limit=${limit}`).then(json<Run[]>),
  getRun: (id: string) => authFetch(`/api/automation-runs/${id}`).then(json<Run>),
  cancelRun: (id: string) =>
    authFetch(`/api/automation-runs/${id}/cancel`, { method: "POST" }).then(json<Run>),
  getVocabulary: () => authFetch("/api/automations/vocabulary").then(json<Vocabulary>),
  draftAutomation: (description: string) =>
    authFetch("/api/automations/draft", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ description }),
    }).then(json<AutomationDraft>),

  // Leads view (Module 9)
  listLeads: (params: LeadQuery = {}) =>
    authFetch(`/api/leads${queryString(params as Record<string, unknown>)}`).then(
      json<LeadPage>,
    ),
  getLeadFacets: () => authFetch("/api/leads/facets").then(json<LeadFacets>),
  createLead: (body: LeadCreate) =>
    authFetch("/api/leads", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<Lead>),
  getLead: (id: string) => authFetch(`/api/leads/${id}`).then(json<Lead>),
  patchLead: (id: string, body: LeadPatch) =>
    authFetch(`/api/leads/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<Lead>),
  getLeadSummary: (id: string) =>
    authFetch(`/api/leads/${id}/summary`).then(json<LeadSummary>),
  regenerateLeadSummary: (id: string) =>
    authFetch(`/api/leads/${id}/summary/regenerate`, { method: "POST" }).then(
      json<LeadSummary>,
    ),
  getLeadMetrics: () => authFetch("/api/leads/metrics").then(json<LeadMetrics>),

  // Caregivers view (Module 10)
  listApplicants: (params: ApplicantQuery = {}) =>
    authFetch(`/api/applicants${queryString(params as Record<string, unknown>)}`).then(
      json<ApplicantPage>,
    ),
  getApplicantFacets: () =>
    authFetch("/api/applicants/facets").then(json<ApplicantFacets>),
  createApplicant: (body: ApplicantCreate) =>
    authFetch("/api/applicants", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<Applicant>),
  getApplicant: (id: string) =>
    authFetch(`/api/applicants/${id}`).then(json<Applicant>),
  patchApplicant: (id: string, body: ApplicantPatch) =>
    authFetch(`/api/applicants/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<Applicant>),
  getApplicantSummary: (id: string) =>
    authFetch(`/api/applicants/${id}/summary`).then(json<ApplicantSummary>),
  regenerateApplicantSummary: (id: string) =>
    authFetch(`/api/applicants/${id}/summary/regenerate`, { method: "POST" }).then(
      json<ApplicantSummary>,
    ),
  getApplicantMetrics: () =>
    authFetch("/api/applicants/metrics").then(json<ApplicantMetrics>),

  // Schedule board (Module 12)
  getScheduleWeek: (week: string) =>
    authFetch(`/api/schedule${queryString({ week })}`).then(json<ScheduleBoard>),
  createVisits: (body: ScheduleCreate) =>
    authFetch("/api/schedules", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<SchedulesCreated>),
  patchVisit: (id: string, body: SchedulePatch) =>
    authFetch(`/api/schedules/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<ScheduleVisit>),
  callOutVisit: (id: string) =>
    authFetch(`/api/schedules/${id}/call-out`, { method: "POST" }).then(
      json<CallOutResult>,
    ),
  assignVisit: (id: string, resourceId: string) =>
    authFetch(`/api/schedules/${id}/assign`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ resource_id: resourceId }),
    }).then(json<AssignResult>),
  cancelVisit: (id: string) =>
    authFetch(`/api/schedules/${id}/cancel`, { method: "POST" }).then(
      json<ScheduleVisit>,
    ),
  getCandidates: (id: string) =>
    authFetch(`/api/schedules/${id}/candidates`).then(json<CandidatesOut>),
  getRoster: (week?: string) =>
    authFetch(`/api/roster${queryString({ week })}`).then(json<CaregiverRoster[]>),
  patchRosterMember: (id: string, body: RosterPatch) =>
    authFetch(`/api/roster/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<CaregiverRoster>),
  notifyCaregiver: (id: string, resourceId: string, message: string) =>
    authFetch(`/api/schedules/${id}/notify`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ resource_id: resourceId, message }),
    }).then(json<NotifyResult>),
  // EVV clock (M16a). Body time is optional — omitted means now. Check-out also
  // completes the visit. Both return the refreshed visit.
  checkInVisit: (id: string, time?: string) =>
    authFetch(`/api/schedules/${id}/check-in`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ time: time ?? null }),
    }).then(json<ScheduleVisit>),
  checkOutVisit: (id: string, time?: string) =>
    authFetch(`/api/schedules/${id}/check-out`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ time: time ?? null }),
    }).then(json<ScheduleVisit>),

  // Clients view (Module 16)
  listClients: (params: ClientQuery = {}) =>
    authFetch(`/api/clients${queryString(params as Record<string, unknown>)}`).then(
      json<ClientPage>,
    ),
  getClientMetrics: (week?: string) =>
    authFetch(`/api/clients/metrics${queryString({ week })}`).then(json<CensusMetrics>),
  getClientFacets: () => authFetch("/api/clients/facets").then(json<ClientFacets>),
  createClient: (body: ClientCreate) =>
    authFetch("/api/clients", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<Client>),
  getClient: (id: string) => authFetch(`/api/clients/${id}`).then(json<ClientDetail>),
  getClientVisits: (id: string, opts?: { upcoming?: number; past?: number }) =>
    authFetch(`/api/clients/${id}/visits${queryString({ ...opts })}`).then(
      json<ClientVisits>,
    ),
  patchClient: (id: string, body: ClientPatch) =>
    authFetch(`/api/clients/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<ClientDetail>),
  getClientSummary: (id: string) =>
    authFetch(`/api/clients/${id}/summary`).then(json<ClientSummary>),
  regenerateClientSummary: (id: string) =>
    authFetch(`/api/clients/${id}/summary/regenerate`, { method: "POST" }).then(
      json<ClientSummary>,
    ),
  createContact: (clientId: string, body: ClientContactCreate) =>
    authFetch(`/api/clients/${clientId}/contacts`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<ClientContact>),
  patchContact: (clientId: string, contactId: string, body: ClientContactPatch) =>
    authFetch(`/api/clients/${clientId}/contacts/${contactId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<ClientContact>),
  deleteContact: (clientId: string, contactId: string) =>
    authFetch(`/api/clients/${clientId}/contacts/${contactId}`, {
      method: "DELETE",
    }).then((r) => {
      if (!r.ok && r.status !== 204) throw new Error(`delete failed: ${r.status}`);
    }),

  // Referrals dashboard (Module 17)
  getReferralMetrics: (months?: number) =>
    authFetch(`/api/referrals/metrics${queryString({ months })}`).then(
      json<ReferralMetrics>,
    ),
  listPartners: () => authFetch("/api/referrals/partners").then(json<Partner[]>),
  createPartner: (body: PartnerCreate) =>
    authFetch("/api/referrals/partners", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<Partner>),
  patchPartner: (id: string, body: PartnerPatch) =>
    authFetch(`/api/referrals/partners/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<Partner>),
  deletePartner: (id: string) =>
    authFetch(`/api/referrals/partners/${id}`, { method: "DELETE" }).then((r) => {
      if (!r.ok && r.status !== 204) throw new Error(`delete failed: ${r.status}`);
    }),
};
