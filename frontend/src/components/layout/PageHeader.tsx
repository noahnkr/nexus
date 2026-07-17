import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

// The single page-title bar across every view. Replaces the hand-rolled h-14
// headers so title, description, and an optional action share one rhythm.
export function PageHeader({
  title,
  description,
  action,
  className,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-4 border-b px-6 py-4",
        className,
      )}
    >
      <div className="min-w-0">
        <h1 className="truncate text-[15px] font-semibold tracking-tight">
          {title}
        </h1>
        {description && (
          <p className="mt-0.5 truncate text-[13px] text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
    </div>
  );
}
