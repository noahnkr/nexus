// SSE over fetch POST. EventSource can't POST a body, so we read the response
// stream ourselves and parse `event:`/`data:` frames separated by a blank line.
import { authFetch, type Source } from "./api";

export interface ChatHandlers {
  onStart?: (d: { thread_id: string; user_message_id: string }) => void;
  onTool?: (d: { name: string; label: string; tool_use_id: string }) => void;
  onToolResult?: (d: {
    tool_use_id: string;
    summary: string;
    is_error: boolean;
    sources?: Source[] | null;
    queued?: boolean;
  }) => void;
  onCitations?: (d: { sources: Source[] }) => void;
  onText?: (d: { delta: string }) => void;
  onDone?: (d: { assistant_message_id: string; usage: Record<string, number> }) => void;
  onError?: (d: { message: string }) => void;
}

export async function streamChat(
  threadId: string,
  content: string,
  handlers: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await authFetch(`/api/chat/threads/${threadId}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ content }),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`chat request failed: ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (frame: string) => {
    let event = "message";
    let data = "";
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!data) return;
    const parsed = JSON.parse(data);
    switch (event) {
      case "start":
        handlers.onStart?.(parsed);
        break;
      case "tool":
        handlers.onTool?.(parsed);
        break;
      case "tool_result":
        handlers.onToolResult?.(parsed);
        break;
      case "citations":
        handlers.onCitations?.(parsed);
        break;
      case "text":
        handlers.onText?.(parsed);
        break;
      case "done":
        handlers.onDone?.(parsed);
        break;
      case "error":
        handlers.onError?.(parsed);
        break;
    }
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      if (frame.trim()) dispatch(frame);
    }
  }
  if (buffer.trim()) dispatch(buffer);
}
