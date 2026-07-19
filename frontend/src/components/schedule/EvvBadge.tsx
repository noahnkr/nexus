import { AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { evvLabel } from "@/lib/schedule";
import { cn } from "@/lib/utils";

// The read-time EVV flag ('late' | 'missed') from the server, rendered as a compact
// amber badge. Renders nothing when there's no flag — a visit that's clocked in, or
// still inside its grace window, has no badge. `compact` drops the icon for the
// tight board chip.
export function EvvBadge({
  evv,
  compact = false,
  className,
}: {
  evv: string | null | undefined;
  compact?: boolean;
  className?: string;
}) {
  const label = evvLabel(evv);
  if (!label) return null;
  return (
    <Badge variant="warning" className={cn("gap-1", compact && "px-1.5 py-0", className)}>
      {!compact && <AlertTriangle className="h-3 w-3" />}
      {label}
    </Badge>
  );
}
