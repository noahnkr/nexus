import { FileText } from "lucide-react";
import type { Source } from "@/lib/api";

export function SourceList({ sources }: { sources: Source[] }) {
  if (!sources || sources.length === 0) return null;
  return (
    <div className="mt-3 border-t pt-3">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Sources
      </div>
      <ul className="flex flex-col gap-2">
        {sources.map((s) => (
          <li key={s.chunk_id} className="flex gap-2 text-xs">
            <span className="font-mono text-muted-foreground">[{s.n}]</span>
            <div className="min-w-0">
              <div className="flex items-center gap-1 font-medium">
                <FileText className="h-3 w-3 shrink-0" />
                <span className="truncate">{s.filename}</span>
                <span className="text-muted-foreground">· chunk {s.chunk_index}</span>
              </div>
              <p className="mt-0.5 line-clamp-2 text-muted-foreground">{s.snippet}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
