import type { ComponentType, ReactNode } from "react";
import { cn } from "@/lib/utils";

// A calm placeholder for empty lists — an icon, a plain-language message, and an
// optional call to action. Replaces bare muted-text divs so every empty surface
// looks intentional rather than broken.
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ComponentType<{ className?: string }>;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed px-6 py-12 text-center",
        className,
      )}
    >
      {Icon && (
        <div className="flex h-11 w-11 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Icon className="h-5 w-5" />
        </div>
      )}
      <div className="space-y-1">
        <p className="text-sm font-medium">{title}</p>
        {description && (
          <p className="mx-auto max-w-sm text-[13px] text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {action}
    </div>
  );
}
