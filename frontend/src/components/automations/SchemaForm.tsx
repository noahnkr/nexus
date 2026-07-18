import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { TokenField, type FieldContext } from "./FieldPicker";
import type { JSONSchema, JSONSchemaProp } from "@/lib/api";

const selectClass =
  "h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

// Renders a JSON Schema's `properties` into form fields (string/number/boolean/
// enum). Every text field is a token input (chips + field picker) so references are
// inserted, never typed. Unknown schema constructs degrade to a raw-JSON textarea
// rather than blocking a save the server would accept — the server 422 is the
// validator of record. Used for tool `input` and function `args`.
export function SchemaForm({
  schema,
  value,
  onChange,
  ctx,
}: {
  schema: JSONSchema;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  ctx: FieldContext;
}) {
  const props = schema?.properties ?? {};
  const required = new Set(schema?.required ?? []);
  const entries = Object.entries(props);

  if (entries.length === 0) {
    return <p className="text-xs text-muted-foreground">No inputs.</p>;
  }

  const set = (key: string, v: unknown) => onChange({ ...value, [key]: v });

  return (
    <div className="space-y-2.5">
      {entries.map(([key, prop]) => (
        <Field
          key={key}
          name={key}
          prop={prop}
          required={required.has(key)}
          value={value[key]}
          onChange={(v) => set(key, v)}
          ctx={ctx}
        />
      ))}
    </div>
  );
}

function Field({
  name,
  prop,
  required,
  value,
  onChange,
  ctx,
}: {
  name: string;
  prop: JSONSchemaProp;
  required: boolean;
  value: unknown;
  onChange: (v: unknown) => void;
  ctx: FieldContext;
}) {
  const label = (
    <label className="mb-1 flex items-center gap-1 text-xs font-medium text-muted-foreground">
      {name.replace(/_/g, " ")}
      {required && <span className="text-destructive">*</span>}
      {prop.description && (
        <span className="font-normal text-muted-foreground/70">— {prop.description}</span>
      )}
    </label>
  );

  // enum -> select
  if (prop.enum && prop.enum.length > 0) {
    return (
      <div>
        {label}
        <select
          className={selectClass}
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value || undefined)}
        >
          <option value="">—</option>
          {prop.enum.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </div>
    );
  }

  // boolean -> checkbox
  if (prop.type === "boolean") {
    return (
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={value === true}
          onChange={(e) => onChange(e.target.checked)}
        />
        {name.replace(/_/g, " ")}
      </label>
    );
  }

  // number/integer -> native number input (kept when NOT templated, so a plain
  // number stays a real number; a templated value switches to the token input).
  if ((prop.type === "integer" || prop.type === "number") && !isTemplate(value)) {
    return (
      <div>
        {label}
        <Input
          type="number"
          value={value === undefined || value === null ? "" : String(value)}
          onChange={(e) => {
            const raw = e.target.value;
            onChange(raw === "" ? undefined : Number(raw));
          }}
        />
      </div>
    );
  }

  // string (or a templated number) -> token input with a field picker
  if (!prop.type || prop.type === "string" || prop.type === "integer" || prop.type === "number") {
    return (
      <div>
        {label}
        <TokenField
          value={value === undefined || value === null ? "" : String(value)}
          onChange={(v) => onChange(v || undefined)}
          ctx={ctx}
          placeholder={prop.type === "string" ? "" : "value or a field"}
        />
      </div>
    );
  }

  // unknown construct -> raw JSON, never a crash
  return (
    <div>
      {label}
      <Textarea
        value={typeof value === "string" ? value : JSON.stringify(value ?? "")}
        onChange={(e) => {
          try {
            onChange(JSON.parse(e.target.value));
          } catch {
            onChange(e.target.value);
          }
        }}
      />
    </div>
  );
}

function isTemplate(v: unknown): boolean {
  return typeof v === "string" && v.includes("{{");
}
