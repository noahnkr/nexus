import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  api,
  type ContentBlock,
  type Source,
  type ThreadOut,
  type ToolCall,
} from "@/lib/api";
import { streamChat } from "@/lib/sse";
import { PageHeader } from "@/components/layout/PageHeader";
import { ThreadList } from "@/components/chat/ThreadList";
import { MessageList, type UIMessage } from "@/components/chat/MessageList";
import type { UITool } from "@/components/chat/ToolActivity";
import { MessageInput } from "@/components/chat/MessageInput";

function blocksToText(content: ContentBlock[]): string {
  return content
    .filter((b) => b.type === "text" && typeof b.text === "string")
    .map((b) => b.text as string)
    .join("");
}

// Rebuild tool chips from the final assistant message's stored metadata, so a
// reloaded thread shows the same tool activity as the live stream did.
function toolsFromMeta(msgId: string, metadata: Record<string, unknown>): UITool[] {
  const calls = (metadata?.tool_calls as ToolCall[] | undefined) ?? [];
  return calls.map((c, i) => ({
    id: `${msgId}-${i}`,
    label: c.summary,
    status: c.is_error ? "error" : c.queued ? "queued" : "done",
  }));
}

export function ChatPage() {
  const [threads, setThreads] = useState<ThreadOut[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const localId = useRef(0);
  const nextId = () => `local-${localId.current++}`;

  const refreshThreads = useCallback(async () => {
    const list = await api.listThreads();
    setThreads(list);
    return list;
  }, []);

  useEffect(() => {
    refreshThreads().catch((e) => toast.error(String(e)));
  }, [refreshThreads]);

  const selectThread = useCallback(async (id: string) => {
    setActiveId(id);
    try {
      const msgs = await api.listMessages(id);
      setMessages(
        // Hide intermediate tool-use / tool_result messages (no visible text);
        // only human questions and assistant answers are shown.
        msgs
          .filter((m) => blocksToText(m.content).trim().length > 0)
          .map((m) => ({
            id: m.id,
            role: m.role,
            text: blocksToText(m.content),
            citations: m.citations ?? [],
            tools: toolsFromMeta(m.id, m.metadata),
          })),
      );
    } catch (e) {
      toast.error(String(e));
    }
  }, []);

  const newThread = () => {
    setActiveId(null);
    setMessages([]);
  };

  const deleteThread = async (id: string) => {
    try {
      await api.deleteThread(id);
      if (id === activeId) newThread();
      await refreshThreads();
    } catch (e) {
      toast.error(String(e));
    }
  };

  const patchLastAssistant = (fn: (m: UIMessage) => UIMessage) => {
    setMessages((prev) => {
      const next = [...prev];
      for (let i = next.length - 1; i >= 0; i--) {
        if (next[i].role === "assistant") {
          next[i] = fn(next[i]);
          break;
        }
      }
      return next;
    });
  };

  const send = async (text: string) => {
    setStreaming(true);

    // Ensure a thread exists.
    let threadId = activeId;
    if (!threadId) {
      try {
        const thread = await api.createThread(text.slice(0, 60));
        threadId = thread.id;
        setActiveId(thread.id);
        await refreshThreads();
      } catch (e) {
        toast.error(String(e));
        setStreaming(false);
        return;
      }
    }

    setMessages((prev) => [
      ...prev,
      { id: nextId(), role: "user", text, citations: [] },
      { id: nextId(), role: "assistant", text: "", citations: [], tools: [], streaming: true },
    ]);

    // Buffer incoming text deltas and flush at most once per animation frame:
    // collapses re-render-per-token into re-render-per-frame, which keeps the
    // react-markdown reparse bounded during a fast stream.
    let pending = "";
    let raf: number | null = null;
    const flush = () => {
      raf = null;
      if (!pending) return;
      const chunk = pending;
      pending = "";
      patchLastAssistant((m) => ({ ...m, text: m.text + chunk }));
    };
    const flushNow = () => {
      if (raf != null) {
        cancelAnimationFrame(raf);
        raf = null;
      }
      flush();
    };

    try {
      await streamChat(threadId, text, {
        onTool: ({ tool_use_id, label }) =>
          patchLastAssistant((m) => ({
            ...m,
            tools: [...(m.tools ?? []), { id: tool_use_id, label, status: "running" }],
          })),
        onToolResult: ({ tool_use_id, summary, is_error, queued }) =>
          patchLastAssistant((m) => ({
            ...m,
            tools: (m.tools ?? []).map((t) =>
              t.id === tool_use_id
                ? {
                    ...t,
                    label: summary,
                    status: is_error ? "error" : queued ? "queued" : "done",
                  }
                : t,
            ),
          })),
        onCitations: ({ sources }: { sources: Source[] }) =>
          patchLastAssistant((m) => ({ ...m, citations: sources })),
        onText: ({ delta }) => {
          pending += delta;
          if (raf == null) raf = requestAnimationFrame(flush);
        },
        onDone: ({ assistant_message_id }) => {
          flushNow();
          patchLastAssistant((m) => ({
            ...m,
            id: assistant_message_id,
            streaming: false,
          }));
        },
        onError: ({ message }) => {
          flushNow();
          toast.error(message);
          patchLastAssistant((m) => ({
            ...m,
            text: m.text || `⚠️ ${message}`,
            streaming: false,
          }));
        },
      });
      flushNow();
      await refreshThreads();
    } catch (e) {
      flushNow();
      toast.error(String(e));
      patchLastAssistant((m) => ({ ...m, streaming: false }));
    } finally {
      setStreaming(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1">
      <ThreadList
        threads={threads}
        activeId={activeId}
        onSelect={selectThread}
        onNew={newThread}
        onDelete={deleteThread}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <PageHeader
          title="Chat"
          description="Ask questions and draft actions — grounded in your documents and data."
        />
        <MessageList messages={messages} />
        <MessageInput onSend={send} disabled={streaming} />
      </div>
    </div>
  );
}
