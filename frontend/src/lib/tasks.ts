import {
  CalendarDays,
  ListTodo,
  Mail,
  MessageSquareText,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import type { PendingAction, Task } from "@/lib/api";

// What KIND of thing is this task? Derived from the first pending action's tool,
// so a queued approval reads as "Text message" at a glance instead of forcing the
// user to open it. Plain tasks (no action) are just "Task".
export interface TaskKind {
  icon: LucideIcon;
  label: string;
}

const KIND_BY_TOOL: Record<string, TaskKind> = {
  send_sms: { icon: MessageSquareText, label: "Text message" },
  send_email: { icon: Mail, label: "Email" },
  create_schedule: { icon: CalendarDays, label: "Scheduling" },
  cancel_schedule: { icon: CalendarDays, label: "Scheduling" },
  assign_caregiver: { icon: CalendarDays, label: "Scheduling" },
  record_call_out: { icon: CalendarDays, label: "Scheduling" },
  update_lead_status: { icon: UserRound, label: "Record update" },
  update_client_status: { icon: UserRound, label: "Record update" },
  update_applicant_stage: { icon: UserRound, label: "Record update" },
};

const PLAIN_TASK: TaskKind = { icon: ListTodo, label: "Task" };

export function taskKind(task: Task): TaskKind {
  const first = task.pending_actions[0];
  if (!first) return PLAIN_TASK;
  return KIND_BY_TOOL[first.tool_name] ?? { icon: ListTodo, label: "Action" };
}

// Plain-language names for the gated tools — staff never see raw tool names.
const TOOL_LABELS: Record<string, string> = {
  update_lead_status: "Update lead status",
  update_client_status: "Update client status",
  update_applicant_stage: "Update applicant stage",
  create_schedule: "Schedule a visit",
  cancel_schedule: "Cancel a visit",
  assign_caregiver: "Assign a caregiver",
  record_call_out: "Record a call-out",
  send_sms: "Send a text message",
  send_email: "Send an email",
};

export function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name.replace(/_/g, " ");
}

// Field labels for the drawer's clean rendering of tool_input. Anything not
// listed is humanized ("lead_id" -> "Lead id") rather than shown as raw JSON.
const FIELD_LABELS: Record<string, string> = {
  to: "To",
  body: "Message",
  subject: "Subject",
  status: "New status",
  stage: "New stage",
  notes: "Notes",
  start_time: "Starts",
  end_time: "Ends",
  reason: "Reason",
};

export function fieldLabel(key: string): string {
  const known = FIELD_LABELS[key];
  if (known) return known;
  const words = key.replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : key;
}

// Values worth showing as their own labeled row. Ids are noise in a plain-language
// view (the task title already names the entity) and belong in technical detail.
export function displayFields(action: PendingAction): [string, string][] {
  return Object.entries(action.tool_input)
    .filter(([k, v]) => !k.endsWith("_id") && v !== null && v !== undefined && v !== "")
    .map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v)]);
}
