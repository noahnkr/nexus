import { useEffect, useRef, useState } from "react";
import { Braces } from "lucide-react";

// A tiny popover that inserts a {{path}} template token into a text field. Offers
// the trigger/entity roots as stubs to complete and each earlier step's `save_as`
// as a ready-made {{context.<key>}} reference. Appends to the field value (cursor
// insertion isn't worth the complexity at this scale).
export function TemplateInsert({
  contextKeys,
  onInsert,
}: {
  contextKeys: string[];
  onInsert: (token: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const options: { label: string; token: string }[] = [
    { label: "Trigger field", token: "{{trigger.payload.}}" },
    { label: "Event type", token: "{{trigger.event_type}}" },
    { label: "Entity field", token: "{{entity.}}" },
    ...contextKeys.map((k) => ({ label: `Step result: ${k}`, token: `{{context.${k}}}` })),
  ];

  const insert = (token: string) => {
    onInsert(token);
    setOpen(false);
  };

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 items-center gap-1 rounded-md border border-input px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        title="Insert a template value"
      >
        <Braces className="h-3.5 w-3.5" />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-1 w-52 overflow-hidden rounded-lg border bg-card shadow-lg">
          <p className="border-b px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Insert value
          </p>
          {options.map((o) => (
            <button
              key={o.token}
              type="button"
              onClick={() => insert(o.token)}
              className="flex w-full flex-col items-start px-3 py-1.5 text-left transition-colors hover:bg-muted"
            >
              <span className="text-[13px]">{o.label}</span>
              <span className="font-mono text-[11px] text-muted-foreground">{o.token}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
