import { NavLink, Outlet } from "react-router-dom";
import { ListTodo, MessageSquare, ScrollText, Upload } from "lucide-react";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "./ThemeToggle";

const nav = [
  { to: "/", label: "Chat", icon: MessageSquare, end: true },
  { to: "/ingestion", label: "Ingestion", icon: Upload, end: false },
  { to: "/tasks", label: "Tasks", icon: ListTodo, end: false },
  { to: "/events", label: "Event Log", icon: ScrollText, end: false },
];

export function AppShell() {
  return (
    <div className="flex h-screen w-full overflow-hidden">
      <aside className="flex w-56 shrink-0 flex-col border-r bg-muted/30">
        <div className="flex h-14 items-center gap-2 border-b px-4">
          <div className="h-6 w-6 rounded bg-primary" />
          <span className="font-semibold tracking-tight">Nexus</span>
        </div>
        <nav className="flex flex-col gap-1 p-2">
          {nav.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                )
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto flex flex-col gap-2 p-2">
          <ThemeToggle />
          <div className="px-1 text-xs text-muted-foreground">
            Control Center · v0.1
          </div>
        </div>
      </aside>
      <main className="flex min-w-0 flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  );
}
