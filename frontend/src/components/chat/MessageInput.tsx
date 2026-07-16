import { useState } from "react";
import { SendHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function MessageInput({
  onSend,
  disabled,
}: {
  onSend: (text: string) => void;
  disabled?: boolean;
}) {
  const [value, setValue] = useState("");

  const submit = () => {
    const text = value.trim();
    if (!text || disabled) return;
    onSend(text);
    setValue("");
  };

  return (
    <div className="flex items-end gap-2 border-t p-4">
      <Textarea
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
        className="max-h-40 resize-none"
        disabled={disabled}
      />
      <Button size="icon" onClick={submit} disabled={disabled} aria-label="Send">
        <SendHorizontal className="h-4 w-4" />
      </Button>
    </div>
  );
}
