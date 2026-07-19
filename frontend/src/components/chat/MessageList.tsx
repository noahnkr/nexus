import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowDown, MessagesSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Source } from "@/lib/api";
import { EmptyState } from "@/components/layout/EmptyState";
import { Markdown } from "./Markdown";
import { SourceList } from "./SourceList";
import { ToolActivity, type UITool } from "./ToolActivity";

export interface UIMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  citations: Source[];
  tools?: UITool[];
  streaming?: boolean;
  // The user stopped this turn mid-stream (live, or derived from the persisted
  // message's `metadata.stopped` when the thread is reloaded).
  stopped?: boolean;
}

const PIN_THRESHOLD = 80; // px from the bottom that still counts as "following"

export function MessageList({ messages }: { messages: UIMessage[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  const [showJump, setShowJump] = useState(false);

  const toBottom = useCallback((behavior: ScrollBehavior = "auto") => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior });
  }, []);

  // Only follow the stream when the user is already parked at the bottom, so
  // scrolling up to read earlier text isn't yanked back on every token flush.
  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    const pinned = distance < PIN_THRESHOLD;
    pinnedRef.current = pinned;
    setShowJump(!pinned);
  }, []);

  useEffect(() => {
    if (pinnedRef.current) toBottom("auto");
  }, [messages, toBottom]);

  const jump = () => {
    pinnedRef.current = true;
    setShowJump(false);
    toBottom("smooth");
  };

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <EmptyState
          icon={MessagesSquare}
          title="Ask a question"
          description="Ask about clients, schedules, or your ingested documents. The assistant can look things up and draft actions for your approval."
        />
      </div>
    );
  }

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex h-full flex-col gap-4 overflow-y-auto p-6"
      >
        {messages.map((m) => (
          <div
            key={m.id}
            className={cn(
              "flex",
              m.role === "user" ? "justify-end" : "justify-start",
            )}
          >
            <div
              className={cn(
                "max-w-[78%] rounded-2xl px-4 py-3 text-sm shadow-sm",
                m.role === "user"
                  ? "rounded-br-md bg-primary text-primary-foreground"
                  : "rounded-bl-md border bg-card",
              )}
            >
              {m.role === "assistant" && <ToolActivity tools={m.tools} />}
              {m.role === "assistant" ? (
                <div className="min-w-0">
                  <Markdown text={m.text} />
                  {m.streaming && (
                    <span className="ml-0.5 inline-block animate-pulse align-baseline">
                      ▍
                    </span>
                  )}
                  {m.stopped && !m.streaming && (
                    <span className="ml-1 text-xs italic text-muted-foreground">
                      — stopped
                    </span>
                  )}
                </div>
              ) : (
                <div className="whitespace-pre-wrap break-words">{m.text}</div>
              )}
              {m.role === "assistant" && <SourceList sources={m.citations} />}
            </div>
          </div>
        ))}
      </div>

      {showJump && (
        <button
          onClick={jump}
          className="absolute bottom-4 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full border bg-card px-3 py-1.5 text-[12px] font-medium shadow-md transition-colors hover:bg-muted"
        >
          <ArrowDown className="h-3.5 w-3.5" />
          Jump to latest
        </button>
      )}
    </div>
  );
}
