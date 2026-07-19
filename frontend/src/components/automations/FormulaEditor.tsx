import { useRef } from "react";
import { AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { checkFormula } from "@/lib/formula";
import { TokenText, type TokenTextHandle } from "./TokenText";
import { FieldPicker, type FieldContext } from "./FieldPicker";

// The editor for a `formula` function step (M15c) — replaces the raw-JSON
// textareas that `weighted_score`'s free-form object args fell back to.
//
// The formula is an ordinary templated string in the recipe, so this is
// TokenText (field chips) plus buttons that insert operators at the caret, plus
// live syntax validation from lib/formula.ts. Validation is advisory: it tells
// the user what's wrong before they save, but the backend parser is what actually
// runs, and it re-checks everything.

const OPERATORS: { label: string; insert: string; title: string }[] = [
  { label: "+", insert: " + ", title: "Add" },
  { label: "−", insert: " - ", title: "Subtract" },
  { label: "×", insert: " * ", title: "Multiply" },
  { label: "÷", insert: " / ", title: "Divide" },
  { label: "( )", insert: "()", title: "Parentheses" },
  { label: "round", insert: "round()", title: "Round to a number of decimals" },
];

export function FormulaEditor({
  value,
  onChange,
  ctx,
}: {
  value: string;
  onChange: (value: string) => void;
  ctx: FieldContext;
}) {
  const inputRef = useRef<TokenTextHandle>(null);
  const text = value ?? "";
  const check = checkFormula(text);
  // An untouched step shouldn't glare red before the user has typed anything.
  const showError = text.trim().length > 0 && !check.ok;

  // TokenText owns the caret and its handle only exposes token insertion, so
  // operators append. Appending is the common case anyway — formulas are built
  // left to right. Operator fragments carry their own padding; bracket fragments
  // get a separating space only when something precedes them.
  const insert = (fragment: string) => {
    const needsSpace =
      text.length > 0 && !text.endsWith(" ") && !fragment.startsWith(" ");
    onChange(text + (needsSpace ? " " : "") + fragment);
  };

  return (
    <div className="space-y-2">
      <div>
        <label className="mb-1 block text-xs font-medium text-muted-foreground">
          Formula
        </label>
        <div className="flex items-center gap-1.5">
          <TokenText
            ref={inputRef}
            value={text}
            onChange={onChange}
            catalog={ctx.vocabulary?.field_catalog}
            contextKeys={ctx.contextKeys}
            placeholder="e.g. hourly rate × 1.5"
            className={cn("flex-1", showError && "border-destructive")}
          />
          <FieldPicker ctx={ctx} onPick={(p) => inputRef.current?.insertToken(p)} />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1">
        {OPERATORS.map((op) => (
          <button
            key={op.label}
            type="button"
            title={op.title}
            onClick={() => insert(op.insert)}
            className="rounded-md border bg-background px-2 py-1 text-xs font-medium text-foreground transition-colors hover:bg-accent"
          >
            {op.label}
          </button>
        ))}
      </div>

      {showError ? (
        <p className="flex items-start gap-1.5 text-xs text-destructive">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {check.error}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">
          Pick fields with the + button and combine them with the operators. Every
          field you use must hold a number.
        </p>
      )}
    </div>
  );
}
