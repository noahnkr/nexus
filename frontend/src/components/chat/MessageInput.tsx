import { useEffect, useRef, useState } from "react";
import { SendHorizontal, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

const MAX_HEIGHT = 160; // matches max-h-40; beyond this the textarea scrolls

// While a turn is streaming the send button becomes a stop button rather than
// going disabled — stopping is the useful action mid-stream, and a dead button
// there was the whole complaint. Input and button are both h-10 so a one-line
// composer reads as a single control; items-end keeps the button pinned to the
// bottom edge once the textarea grows.
export function MessageInput({
  onSend,
  onStop,
  streaming,
}: {
  onSend: (text: string) => void;
  onStop?: () => void;
  streaming?: boolean;
}) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  // Grow with the content up to MAX_HEIGHT (textareas can't do this in CSS).
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [value]);

  const submit = () => {
    const text = value.trim();
    if (!text || streaming) return;
    onSend(text);
    setValue("");
  };

  return (
    <div className="flex items-end gap-2 border-t p-4">
      <Textarea
        ref={ref}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder="Ask about your documents…  (Enter to send, Shift+Enter for newline)"
        rows={1}
        className="h-10 max-h-40 min-h-10 resize-none py-2 leading-5"
      />
      {streaming ? (
        <Button
          size="icon"
          variant="outline"
          className="h-10 w-10 shrink-0"
          onClick={onStop}
          aria-label="Stop generating"
          title="Stop generating"
        >
          <Square className="h-3.5 w-3.5 fill-current" />
        </Button>
      ) : (
        <Button
          size="icon"
          className="h-10 w-10 shrink-0"
          onClick={submit}
          disabled={!value.trim()}
          aria-label="Send"
        >
          <SendHorizontal className="h-4 w-4" />
        </Button>
      )}
    </div>
  );
}
