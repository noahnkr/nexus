import type { TaskPriority, TaskStatus } from "@/lib/api";

// Shared badge/dot vocabulary for tasks. Extracted from TaskCard so the card, the
// drawer, the filters, and the create dialog all read priority and status the same
// way — one definition, no per-surface drift.

export const PRIORITY_VARIANT: Record<
  TaskPriority,
  "default" | "secondary" | "destructive" | "outline"
> = {
  urgent: "destructive",
  high: "default",
  normal: "secondary",
  low: "outline",
};

// Leading-dot colors for the priority Select (Module 13), mirroring the badge
// variant tones so priority reads the same in filters, dialogs, and cards.
export const PRIORITY_DOT: Record<TaskPriority, string> = {
  urgent: "bg-destructive",
  high: "bg-primary",
  normal: "bg-muted-foreground",
  low: "bg-muted-foreground/40",
};

export const STATUS_VARIANT: Record<
  TaskStatus,
  "secondary" | "outline" | "success" | "warning" | "info"
> = {
  pending: "warning",
  in_progress: "info",
  done: "success",
  cancelled: "outline",
};

export const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: "Pending",
  in_progress: "In progress",
  done: "Done",
  cancelled: "Cancelled",
};
