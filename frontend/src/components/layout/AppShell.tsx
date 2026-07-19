import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import {
  BookOpen,
  CalendarDays,
  Filter,
  Handshake,
  HeartPulse,
  Home,
  ListTodo,
  Menu,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  ScrollText,
  Users,
  X,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { UserMenu } from "./UserMenu";

// Tasks sits above Knowledge: it's the daily-triage surface, Knowledge is
// occasional. Automations (the Center) sits between Tasks and Knowledge. Home
// lands at "/"; Chat moved to "/chat". Settings is reached from the UserMenu,
// not the nav — it's a per-person destination, not a daily one.
const nav = [
  { to: "/", label: "Home", icon: Home, end: true },
  { to: "/chat", label: "Chat", icon: MessageSquare, end: false },
  { to: "/tasks", label: "Tasks", icon: ListTodo, end: false },
  { to: "/leads", label: "Leads", icon: Filter, end: false },
  { to: "/referrals", label: "Referrals", icon: Handshake, end: false },
  { to: "/caregivers", label: "Caregivers", icon: Users, end: false },
  { to: "/clients", label: "Clients", icon: HeartPulse, end: false },
  { to: "/schedule", label: "Schedule", icon: CalendarDays, end: false },
  { to: "/automations", label: "Automations", icon: Zap, end: false },
  { to: "/knowledge", label: "Knowledge", icon: BookOpen, end: false },
  { to: "/events", label: "Event Log", icon: ScrollText, end: false },
];

const COLLAPSE_KEY = "nexus.sidebar";

function BrandMark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm",
        className,
      )}
    >
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
  );
}

// One definition of the sidebar's insides, rendered twice: as the desktop rail
// (collapsible to icons) and as the mobile drawer (always expanded). Keeping it
// shared means a nav change can't drift between the two.
function SidebarBody({
  collapsed,
  onNavigate,
  header,
}: {
  collapsed: boolean;
  onNavigate?: () => void;
  header: React.ReactNode;
}) {
  return (
    <>
      {header}

      <nav className={cn("flex flex-1 flex-col gap-0.5 py-2", collapsed ? "px-2" : "px-2.5")}>
        {nav.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={onNavigate}
            title={collapsed ? label : undefined}
            className={({ isActive }) =>
              cn(
                "group relative flex items-center rounded-md py-2 text-[13px] font-medium transition-colors",
                collapsed ? "justify-center px-0" : "gap-2.5 px-2.5",
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
                {!collapsed && label}
                {collapsed && <span className="sr-only">{label}</span>}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <div className={cn("border-t", collapsed ? "p-2" : "p-2.5")}>
        <UserMenu compact={collapsed} onNavigate={onNavigate} />
      </div>
    </>
  );
}

export function AppShell() {
  // Desktop rail collapse, remembered across reloads. Read lazily so the first
  // paint is already in the right state (no flash of the wide rail).
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(COLLAPSE_KEY) === "collapsed",
  );
  const [drawerOpen, setDrawerOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    localStorage.setItem(COLLAPSE_KEY, collapsed ? "collapsed" : "expanded");
  }, [collapsed]);

  // Belt and braces on top of the per-link onNavigate: any route change closes
  // the drawer, including ones triggered from inside a page.
  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawerOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  // 100dvh, not 100vh: on mobile browsers the dynamic viewport unit accounts for
  // the collapsing address bar, so full-height pages don't overflow.
  return (
    <div className="flex h-[100dvh] w-full overflow-hidden bg-background">
      {/* Desktop rail (>= md) */}
      <aside
        className={cn(
          "hidden shrink-0 flex-col border-r bg-muted/40 transition-[width] duration-200 md:flex",
          collapsed ? "w-14" : "w-60",
        )}
      >
        <SidebarBody
          collapsed={collapsed}
          header={
            <div
              className={cn(
                "flex h-14 items-center",
                collapsed ? "justify-center px-2" : "gap-2.5 px-4",
              )}
            >
              {!collapsed && <BrandMark />}
              {!collapsed && (
                <span className="flex min-w-0 flex-col leading-none">
                  <span className="text-[15px] font-semibold tracking-tight">Nexus</span>
                  <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                    Control Center
                  </span>
                </span>
              )}
              <button
                onClick={() => setCollapsed((v) => !v)}
                title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                className={cn(
                  "flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-card hover:text-foreground",
                  !collapsed && "ml-auto",
                )}
              >
                {collapsed ? (
                  <PanelLeftOpen className="h-4 w-4" />
                ) : (
                  <PanelLeftClose className="h-4 w-4" />
                )}
              </button>
            </div>
          }
        />
      </aside>

      {/* Mobile drawer (< md) */}
      {drawerOpen && (
        <div className="fixed inset-0 z-50 md:hidden">
          <div
            className="absolute inset-0 bg-black/40"
            onClick={() => setDrawerOpen(false)}
          />
          <aside className="absolute left-0 top-0 flex h-full w-64 flex-col border-r bg-card shadow-xl">
            <SidebarBody
              collapsed={false}
              onNavigate={() => setDrawerOpen(false)}
              header={
                <div className="flex h-14 items-center gap-2.5 px-4">
                  <BrandMark />
                  <span className="flex min-w-0 flex-col leading-none">
                    <span className="text-[15px] font-semibold tracking-tight">Nexus</span>
                    <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                      Control Center
                    </span>
                  </span>
                  <button
                    onClick={() => setDrawerOpen(false)}
                    aria-label="Close menu"
                    className="ml-auto flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              }
            />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile top bar (< md) — the rail's stand-in */}
        <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3 md:hidden">
          <button
            onClick={() => setDrawerOpen(true)}
            aria-label="Open menu"
            className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <Menu className="h-5 w-5" />
          </button>
          <BrandMark className="h-7 w-7" />
          <span className="text-sm font-semibold tracking-tight">Nexus</span>
          <div className="ml-auto">
            <UserMenu compact side="bottom" />
          </div>
        </header>

        <main className="flex min-h-0 flex-1 flex-col">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
