import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { api, type ContentBlock, type Source, type ThreadOut } from "@/lib/api";
import { streamChat } from "@/lib/sse";
import { ThreadList } from "@/components/chat/ThreadList";
import { MessageList, type UIMessage } from "@/components/chat/MessageList";
import { MessageInput } from "@/components/chat/MessageInput";

function blocksToText(content: ContentBlock[]): string {
  return content
    .filter((b) => b.type === "text" && typeof b.text === "string")
    .map((b) => b.text as string)
    .join("");
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
        msgs.map((m) => ({
          id: m.id,
          role: m.role,
          text: blocksToText(m.content),
          citations: m.citations ?? [],
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
      { id: nextId(), role: "assistant", text: "", citations: [], streaming: true },
    ]);

    try {
      await streamChat(threadId, text, {
        onCitations: ({ sources }: { sources: Source[] }) =>
          patchLastAssistant((m) => ({ ...m, citations: sources })),
        onText: ({ delta }) =>
          patchLastAssistant((m) => ({ ...m, text: m.text + delta })),
        onDone: ({ assistant_message_id }) =>
          patchLastAssistant((m) => ({
            ...m,
            id: assistant_message_id,
            streaming: false,
          })),
        onError: ({ message }) => {
          toast.error(message);
          patchLastAssistant((m) => ({
            ...m,
            text: m.text || `⚠️ ${message}`,
            streaming: false,
          }));
        },
      });
      await refreshThreads();
    } catch (e) {
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
        <header className="flex h-14 items-center border-b px-6">
          <h1 className="text-lg font-semibold">Chat</h1>
        </header>
        <MessageList messages={messages} />
        <MessageInput onSend={send} disabled={streaming} />
      </div>
    </div>
  );
}
