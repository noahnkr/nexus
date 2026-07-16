import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ThreadOut } from "@/lib/api";

export function ThreadList({
  threads,
  activeId,
  onSelect,
  onNew,
  onDelete,
}: {
  threads: ThreadOut[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="flex w-60 shrink-0 flex-col border-r">
      <div className="p-3">
        <Button className="w-full" onClick={onNew}>
          <Plus className="h-4 w-4" />
          New thread
        </Button>
      </div>
      <div className="flex flex-1 flex-col gap-1 overflow-y-auto px-2 pb-2">
        {threads.map((t) => (
          <div
            key={t.id}
            onClick={() => onSelect(t.id)}
            className={cn(
              "group flex cursor-pointer items-center justify-between gap-2 rounded-md px-3 py-2 text-sm",
              t.id === activeId ? "bg-accent" : "hover:bg-muted/60",
            )}
          >
            <span className="truncate">{t.title || "Untitled thread"}</span>
            <button
              className="opacity-0 transition-opacity group-hover:opacity-100"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(t.id);
              }}
              aria-label="Delete thread"
            >
              <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-destructive" />
            </button>
          </div>
        ))}
        {threads.length === 0 && (
          <div className="px-3 py-6 text-center text-xs text-muted-foreground">
            No threads yet.
          </div>
        )}
      </div>
    </div>
  );
}
