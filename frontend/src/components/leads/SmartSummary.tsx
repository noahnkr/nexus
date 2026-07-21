import { useCallback, useEffect, useRef, useState } from "react";
import { LucideIcon, RefreshCw, Sparkles } from "lucide-react";
import { parseApiError, relativeTime } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

interface SummaryResult {
  summary: string;
  generated_at: string;
}

// AI smart summary — cached server-side (WS7): the first open generates + persists
// it; later opens load the cached row instantly. The Regenerate button forces a
// refresh. A 503 (no Anthropic key, nothing cached) shows a quiet inline notice and
// never blocks the page.
//
// Generic on purpose (Module 10): the caller supplies `getSummary`/`regenerateSummary`
// closures over its own entity, so Leads and Caregivers both reuse it verbatim.
// `entityId` is the stable identity that drives the initial fetch — the closures may
// be fresh each render without re-triggering.
export function SmartSummary({
  entityId,
  getSummary,
  regenerateSummary,
  label = "Smart summary",
  icon: Icon = Sparkles,
  unavailableText = "AI summaries are unavailable — no language-model key is configured.",
}: {
  entityId: string;
  getSummary: () => Promise<SummaryResult>;
  regenerateSummary: () => Promise<SummaryResult>;
  label?: string;
  icon?: LucideIcon;
  unavailableText?: string;
}) {
  const fns = useRef({ getSummary, regenerateSummary });
  fns.current = { getSummary, regenerateSummary };

  const [summary, setSummary] = useState<string | null>(null);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (refresh: boolean) => {
    setLoading(true);
    setError(null);
    setUnavailable(false);
    try {
      const res = refresh
        ? await fns.current.regenerateSummary()
        : await fns.current.getSummary();
      setSummary(res.summary);
      setGeneratedAt(res.generated_at);
    } catch (e) {
      const { status } = parseApiError(e);
      if (status === 503) setUnavailable(true);
      else setError("Couldn't generate a summary right now.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(false); // cached when available, generates once on first open
  }, [entityId, load]);

  return (
    <Card className="border-primary/20 bg-primary/[0.03]">
      <CardContent className="p-4">
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Icon className="h-3.5 w-3.5 text-primary" />
            {label}
            {generatedAt && !loading && (
              <span className="font-normal">· {relativeTime(generatedAt)}</span>
            )}
          </div>
          {!unavailable && (
            <button
              onClick={() => void load(true)}
              disabled={loading}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
            >
              <RefreshCw className={loading ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />
              Regenerate
            </button>
          )}
        </div>

        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-11/12" />
            <Skeleton className="h-4 w-3/4" />
          </div>
        ) : unavailable ? (
          <p className="text-sm text-muted-foreground">{unavailableText}</p>
        ) : error ? (
          <p className="text-sm text-muted-foreground">{error}</p>
        ) : (
          <p className="text-sm leading-relaxed text-foreground">{summary}</p>
        )}
      </CardContent>
    </Card>
  );
}
