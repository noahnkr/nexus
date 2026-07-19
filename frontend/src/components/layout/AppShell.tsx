import { NavLink, Outlet } from "react-router-dom";
import {
  CalendarDays,
  Filter,
  Home,
  ListTodo,
  MessageSquare,
  ScrollText,
  Upload,
  Users,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { UserMenu } from "./UserMenu";

// Tasks sits above Ingestion: it's the daily-triage surface, Ingestion is
// occasional. Automations (the Center) sits between Tasks and Ingestion. Home
// lands at "/"; Chat moved to "/chat".
const nav = [
  { to: "/", label: "Home", icon: Home, end: true },
  { to: "/chat", label: "Chat", icon: MessageSquare, end: false },
  { to: "/tasks", label: "Tasks", icon: ListTodo, end: false },
  { to: "/leads", label: "Leads", icon: Filter, end: false },
  { to: "/caregivers", label: "Caregivers", icon: Users, end: false },
  { to: "/schedule", label: "Schedule", icon: CalendarDays, end: false },
  { to: "/automations", label: "Automations", icon: Zap, end: false },
  { to: "/ingestion", label: "Ingestion", icon: Upload, end: false },
  { to: "/events", label: "Event Log", icon: ScrollText, end: false },
];

export function AppShell() {
  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      <aside className="flex w-60 shrink-0 flex-col border-r bg-muted/40">
        <div className="flex h-14 items-center gap-2.5 px-4">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
            {/* Nexus mark: a hub node linked to its satellites. */}
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" aria-hidden="true">
              <path
                d="M12 12 L5 5.5 M12 12 L19 6 M12 12 L6 18.5 M12 12 L18.5 18"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                opacity="0.85"
              />
              <circle cx="12" cy="12" r="3" fill="currentColor" />
              <circle cx="5" cy="5.5" r="1.7" fill="currentColor" />
              <circle cx="19" cy="6" r="1.7" fill="currentColor" />
              <circle cx="6" cy="18.5" r="1.7" fill="currentColor" />
              <circle cx="18.5" cy="18" r="1.7" fill="currentColor" />
            </svg>
          </span>
          <span className="flex flex-col leading-none">
            <span className="text-[15px] font-semibold tracking-tight">
              Nexus
            </span>
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Control Center
            </span>
          </span>
        </div>

        <nav className="flex flex-1 flex-col gap-0.5 px-2.5 py-2">
          {nav.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "group relative flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] font-medium transition-colors",
                  isActive
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:bg-card/60 hover:text-foreground",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <span
                    className={cn(
                      "absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-primary transition-opacity",
                      isActive ? "opacity-100" : "opacity-0",
                    )}
                  />
                  <Icon
                    className={cn(
                      "h-4 w-4 shrink-0 transition-colors",
                      isActive
                        ? "text-primary"
                        : "text-muted-foreground group-hover:text-foreground",
                    )}
                  />
                  {label}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="border-t p-2.5">
          <UserMenu />
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  );
}
