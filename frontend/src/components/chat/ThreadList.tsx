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
  className,
}: {
  threads: ThreadOut[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  // Overridden on mobile, where this renders inside an overlay panel instead of
  // as a fixed rail beside the conversation.
  className?: string;
}) {
  return (
    <div className={cn("flex w-60 shrink-0 flex-col border-r bg-muted/20", className)}>
      <div className="p-3">
        <Button className="w-full" size="sm" onClick={onNew}>
          <Plus className="h-4 w-4" />
          New thread
        </Button>
      </div>
      <div className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-2 pb-2">
        {threads.map((t) => (
          <div
            key={t.id}
            onClick={() => onSelect(t.id)}
            className={cn(
              "group flex cursor-pointer items-center justify-between gap-2 rounded-md px-3 py-2 text-[13px] transition-colors",
              t.id === activeId
                ? "bg-card font-medium text-foreground shadow-sm"
                : "text-muted-foreground hover:bg-card/60 hover:text-foreground",
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
