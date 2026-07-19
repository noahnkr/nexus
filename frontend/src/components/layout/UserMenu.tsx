import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { LogOut, Monitor, Moon, Settings, Sun } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";

// The identity + account menu. Shows the signed-in email and pops a small menu
// with Settings, a theme toggle, and sign out. No Radix dropdown in this project,
// so it's a plain popover with a click-outside listener.
//
// Two shapes: the full row for the expanded sidebar, and `compact` (avatar only)
// for the collapsed icon rail and the mobile top bar. `side` flips the popover
// because it opens upward from the sidebar footer but downward from the top bar.
export function UserMenu({
  compact = false,
  side = "top",
  onNavigate,
}: {
  compact?: boolean;
  side?: "top" | "bottom";
  onNavigate?: () => void;
}) {
  const { session, signOut } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const email = session?.user?.email ?? "";
  const initial = email.slice(0, 1).toUpperCase() || "?";

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const isDark = theme === "dark";
  const itemClass =
    "flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] text-foreground transition-colors hover:bg-muted";

  return (
    <div ref={ref} className="relative">
      {open && (
        <div
          className={cn(
            "absolute z-50 overflow-hidden rounded-lg border bg-card shadow-lg",
            side === "top" ? "bottom-full mb-2" : "top-full mt-2",
            // The compact trigger is too narrow to anchor a readable menu, so the
            // popover gets its own width and hangs from the trigger's right edge.
            compact ? "right-0 w-48" : "left-0 right-0",
          )}
        >
          {compact && email && (
            <div className="border-b px-3 py-2">
              <span className="block truncate text-[12px] font-medium">{email}</span>
            </div>
          )}
          <Link
            to="/settings"
            onClick={() => {
              setOpen(false);
              onNavigate?.();
            }}
            className={itemClass}
          >
            <Settings className="h-4 w-4 text-muted-foreground" />
            Settings
          </Link>
          <div className="border-t" />
          <button onClick={() => toggleTheme()} className={itemClass}>
            {isDark ? (
              <Sun className="h-4 w-4 text-muted-foreground" />
            ) : (
              <Moon className="h-4 w-4 text-muted-foreground" />
            )}
            {isDark ? "Light theme" : "Dark theme"}
          </button>
          <div className="border-t" />
          <button onClick={() => signOut()} className={itemClass}>
            <LogOut className="h-4 w-4 text-muted-foreground" />
            Sign out
          </button>
        </div>
      )}

      <button
        onClick={() => setOpen((v) => !v)}
        title={compact ? email || "Account" : undefined}
        aria-label={compact ? "Account menu" : undefined}
        className={cn(
          "flex items-center rounded-lg border border-transparent text-left transition-colors hover:bg-muted",
          compact ? "h-9 w-9 justify-center p-0" : "w-full gap-2.5 px-2 py-2",
          open && "border-border bg-muted",
        )}
      >
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[13px] font-semibold text-primary">
          {initial}
        </span>
        {!compact && (
          <>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-medium leading-tight">
                {email || "Signed in"}
              </span>
              <span className="block truncate text-[11px] leading-tight text-muted-foreground">
                Control Center
              </span>
            </span>
            <Monitor className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          </>
        )}
      </button>
    </div>
  );
}
