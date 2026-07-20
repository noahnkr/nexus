import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/layout/PageHeader";
import { PipelineTab } from "@/components/caregivers/PipelineTab";
import { RosterTab } from "@/components/caregivers/RosterTab";

// ONE people surface (M18b, user-locked): the hiring funnel and the working roster
// are two views of the same population, so they are tabs here rather than a second
// nav entry. The active tab lives in the URL (`/caregivers?tab=roster`) so it can be
// linked to and survives a reload — the Knowledge-page precedent.
type Tab = "pipeline" | "roster";

const TABS: { id: Tab; label: string }[] = [
  { id: "pipeline", label: "Pipeline" },
  { id: "roster", label: "Roster" },
];

export function CaregiversPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: Tab = searchParams.get("tab") === "roster" ? "roster" : "pipeline";
  const [creating, setCreating] = useState(false);

  const setTab = (next: Tab) => {
    const params = new URLSearchParams(searchParams);
    // Pipeline is the default, so it stays out of the URL. Its own filters are
    // meaningless on the Roster tab, so they're dropped on the way over.
    if (next === "pipeline") params.delete("tab");
    else {
      params.set("tab", next);
      params.delete("stage");
      params.delete("source");
      params.delete("q");
    }
    setSearchParams(params, { replace: true });
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Caregivers"
        description={
          tab === "pipeline"
            ? "Your hiring pipeline — every applicant and where they stand."
            : "Your working roster — hours, utilization, and credential compliance."
        }
        action={
          tab === "pipeline" ? (
            <Button size="sm" onClick={() => setCreating(true)}>
              <Plus className="h-4 w-4" /> New applicant
            </Button>
          ) : undefined
        }
      />

      <div className="border-b px-4 sm:px-6">
        <div className="flex gap-1 overflow-x-auto">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                "-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition-colors",
                tab === t.id
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "pipeline" ? (
        <PipelineTab creating={creating} setCreating={setCreating} />
      ) : (
        <RosterTab />
      )}
    </div>
  );
}
