import type { ComponentType } from "react";
import { Link } from "react-router-dom";
import { MessageSquarePlus, Plus, Upload } from "lucide-react";

// The three most common ways to start work from Home. Each lands on the view that
// already owns the flow — New task deep-links Tasks into its create dialog.
const actions: {
  to: string;
  label: string;
  hint: string;
  icon: ComponentType<{ className?: string }>;
}[] = [
  {
    to: "/chat",
    label: "New chat",
    hint: "Ask about clients, schedules, or documents",
    icon: MessageSquarePlus,
  },
  {
    to: "/ingestion",
    label: "Upload document",
    hint: "Add a file to the knowledge base",
    icon: Upload,
  },
  {
    to: "/tasks?create=1",
    label: "New task",
    hint: "Track something that needs doing",
    icon: Plus,
  },
];

export function QuickActions() {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {actions.map(({ to, label, hint, icon: Icon }) => (
        <Link
          key={to}
          to={to}
          className="group flex items-center gap-3 rounded-xl border bg-card p-3.5 shadow-sm transition-all hover:border-primary/40 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
            <Icon className="h-[18px] w-[18px]" />
          </span>
          <span className="min-w-0">
            <span className="block text-[13px] font-medium">{label}</span>
            <span className="block truncate text-[12px] text-muted-foreground">
              {hint}
            </span>
          </span>
        </Link>
      ))}
    </div>
  );
}
