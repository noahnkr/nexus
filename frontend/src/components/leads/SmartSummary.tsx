import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { parseApiError, relativeTime } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

// AI smart summary — cached (WS7): the first open generates + persists it; later
// opens load the cached row instantly. The Regenerate button forces a refresh. A
// 503 (no Anthropic key, nothing cached) shows a quiet inline notice and never
// blocks the page.
export function SmartSummary({ leadId }: { leadId: string }) {
  const [summary, setSummary] = useState<string | null>(null);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (refresh: boolean) => {
      setLoading(true);
      setError(null);
      setUnavailable(false);
      try {
        const res = refresh
          ? await api.regenerateLeadSummary(leadId)
          : await api.getLeadSummary(leadId);
        setSummary(res.summary);
        setGeneratedAt(res.generated_at);
      } catch (e) {
        const { status } = parseApiError(e);
        if (status === 503) setUnavailable(true);
        else setError("Couldn't generate a summary right now.");
      } finally {
        setLoading(false);
      }
    },
    [leadId],
  );

  useEffect(() => {
    void load(false); // cached when available, generates once on first open
  }, [load]);

  return (
    <Card className="border-primary/20 bg-primary/[0.03]">
      <CardContent className="p-4">
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Sparkles className="h-3.5 w-3.5 text-primary" />
            Smart summary
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
          <p className="text-sm text-muted-foreground">
            AI summaries are unavailable — no language-model key is configured.
          </p>
        ) : error ? (
          <p className="text-sm text-muted-foreground">{error}</p>
        ) : (
          <p className="text-sm leading-relaxed text-foreground">{summary}</p>
        )}
      </CardContent>
    </Card>
  );
}
