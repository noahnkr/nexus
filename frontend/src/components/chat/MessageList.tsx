import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import type { Source } from "@/lib/api";
import { SourceList } from "./SourceList";

export interface UIMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  citations: Source[];
  streaming?: boolean;
}

export function MessageList({ messages }: { messages: UIMessage[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        Ask a question about your ingested documents.
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-6">
      {messages.map((m) => (
        <div
          key={m.id}
          className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}
        >
          <div
            className={cn(
              "max-w-[75%] rounded-lg px-4 py-3 text-sm",
              m.role === "user"
                ? "bg-primary text-primary-foreground"
                : "border bg-card",
            )}
          >
            <div className="whitespace-pre-wrap break-words">
              {m.text}
              {m.streaming && <span className="ml-0.5 animate-pulse">▍</span>}
            </div>
            {m.role === "assistant" && <SourceList sources={m.citations} />}
          </div>
        </div>
      ))}
      <div ref={endRef} />
    </div>
  );
}
