import { useEffect, useState } from "react";
import { toast } from "sonner";
import { CalendarDays, CheckCircle2, FileText, ListTodo, ScrollText, Zap } from "lucide-react";
import { api, type EventOut, type HomeSummary } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { StatCard } from "@/components/home/StatCard";
import { QuickActions } from "@/components/home/QuickActions";
import { RecentActivity } from "@/components/home/RecentActivity";

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

function longDate(): string {
  return new Date().toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

// A calm landing page, not a triage queue: a greeting, four at-a-glance counts,
// quick ways to start work, and a glance at recent activity. It establishes the
// widget-grid pattern future modules extend — counts load once on mount (no
// Realtime here by design).
export function HomePage() {
  const { session } = useAuth();
  const name = (session?.user?.email ?? "").split("@")[0] || "there";

  const [summary, setSummary] = useState<HomeSummary | null>(null);
  const [events, setEvents] = useState<EventOut[]>([]);
  const [workspace, setWorkspace] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    // Settings ride along with the existing mount fetches. A failure here must not
    // blank the page, so it degrades to the default greeting.
    api
      .getSettings()
      .then((s) => !cancelled && setWorkspace(s.workspace_name))
      .catch(() => {});
    Promise.all([api.getHomeSummary(), api.listEvents({ limit: 6 })])
      .then(([s, page]) => {
        if (cancelled) return;
        setSummary(s);
        setEvents(page.events);
      })
      .catch((e) => !cancelled && toast.error(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const docs = summary?.documents;
  const docSub =
    docs && (docs.processing || docs.failed)
      ? [
          docs.processing ? `${docs.processing} processing` : null,
          docs.failed ? `${docs.failed} failed` : null,
        ]
          .filter(Boolean)
          .join(" · ")
      : "In the knowledge base";

  const auto = summary?.automations;
  const autoSub = auto?.failed_today
    ? `${auto.failed_today} failed today`
    : auto?.runs_today
      ? `${auto.runs_today} run${auto.runs_today === 1 ? "" : "s"} today`
      : "Active recipes";

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 sm:py-8 md:px-8 md:py-10">
        {/* Greeting */}
        <header className="mb-8">
          <p className="text-[13px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            {longDate()}
          </p>
          <h1 className="mt-1.5 text-[26px] font-semibold tracking-tight">
            {greeting()},{" "}
            <span className="text-primary">{name}</span>
          </h1>
          <p className="mt-1 text-[14px] text-muted-foreground">
            {workspace
              ? `Here's what's happening across ${workspace}.`
              : "Here's what's happening across the Control Center."}
          </p>
        </header>

        {/* At-a-glance counts */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
          <StatCard
            to="/tasks"
            label="Open tasks"
            count={summary?.open_tasks ?? 0}
            icon={ListTodo}
            sublabel="Pending or in progress"
            loading={loading}
          />
          <StatCard
            to="/tasks"
            label="Awaiting approval"
            count={summary?.pending_approvals ?? 0}
            icon={CheckCircle2}
            sublabel="Actions to review"
            tone={summary?.pending_approvals ? "warning" : "default"}
            loading={loading}
          />
          <StatCard
            to="/schedule"
            label="Open shifts"
            count={summary?.open_shifts ?? 0}
            icon={CalendarDays}
            sublabel="Unfilled visits to staff"
            tone={summary?.open_shifts ? "warning" : "default"}
            loading={loading}
          />
          <StatCard
            to="/knowledge"
            label="Documents ready"
            count={docs?.ready ?? 0}
            icon={FileText}
            sublabel={docSub}
            tone={docs?.failed ? "warning" : "default"}
            loading={loading}
          />
          <StatCard
            to="/automations"
            label="Automations"
            count={auto?.active ?? 0}
            icon={Zap}
            sublabel={autoSub}
            tone={auto?.failed_today ? "warning" : "info"}
            loading={loading}
          />
          <StatCard
            to="/events"
            label="Events today"
            count={summary?.events_today ?? 0}
            icon={ScrollText}
            sublabel="Logged since midnight"
            tone="info"
            loading={loading}
          />
        </div>

        {/* Quick actions */}
        <div className="mt-8">
          <h2 className="mb-3 text-[13px] font-semibold text-muted-foreground">
            Quick actions
          </h2>
          <QuickActions />
        </div>

        {/* Recent activity */}
        <div className="mt-8">
          <RecentActivity events={events} loading={loading} />
        </div>
      </div>
    </div>
  );
}
