import { useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, type SelectOption } from "@/components/ui/Select";
import { PRIORITY_DOT } from "./TaskCard";
import type { TaskCreate, TaskPriority } from "@/lib/api";

const PRIORITIES: TaskPriority[] = ["low", "normal", "high", "urgent"];

const PRIORITY_OPTIONS: SelectOption<TaskPriority>[] = PRIORITIES.map((p) => ({
  value: p,
  label: p,
  dot: PRIORITY_DOT[p],
}));

export function CreateTaskDialog({
  open,
  onClose,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (body: TaskCreate) => Promise<void>;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<TaskPriority>("normal");
  const [dueAt, setDueAt] = useState("");
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  const reset = () => {
    setTitle("");
    setDescription("");
    setPriority("normal");
    setDueAt("");
  };

  const submit = async () => {
    if (!title.trim()) return;
    setBusy(true);
    try {
      await onCreate({
        title: title.trim(),
        description: description.trim() || undefined,
        priority,
        due_at: dueAt ? new Date(dueAt).toISOString() : null,
      });
      reset();
      onClose();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border bg-card p-5 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">New task</h2>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Title
            </label>
            <Input
              autoFocus
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="What needs doing?"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Description
            </label>
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional detail or context"
            />
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Priority
              </label>
              <Select
                value={priority}
                onChange={setPriority}
                options={PRIORITY_OPTIONS}
                aria-label="Priority"
              />
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Due (optional)
              </label>
              <input
                type="datetime-local"
                className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                value={dueAt}
                onChange={(e) => setDueAt(e.target.value)}
              />
            </div>
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || !title.trim()}>
            Create task
          </Button>
        </div>
      </div>
    </div>
  );
}
