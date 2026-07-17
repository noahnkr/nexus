import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { TemplateInsert } from "./TemplateInsert";
import type { JSONSchema, JSONSchemaProp } from "@/lib/api";

const selectClass =
  "h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

// Renders a JSON Schema's `properties` into form fields (string/number/boolean/
// enum), with a {{template}} inserter on every text field. Unknown schema
// constructs degrade to a raw-JSON textarea rather than blocking a save the server
// would accept — the server 422 is the validator of record. Used for tool `input`
// and function `args`.
export function SchemaForm({
  schema,
  value,
  onChange,
  contextKeys,
}: {
  schema: JSONSchema;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  contextKeys: string[];
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
          contextKeys={contextKeys}
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
  contextKeys,
}: {
  name: string;
  prop: JSONSchemaProp;
  required: boolean;
  value: unknown;
  onChange: (v: unknown) => void;
  contextKeys: string[];
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

  // number/integer -> number input (kept as string when templated, so {{...}} works)
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

  // string (or a templated number) -> text input with template inserter
  if (!prop.type || prop.type === "string" || prop.type === "integer" || prop.type === "number") {
    return (
      <div>
        {label}
        <div className="flex gap-1.5">
          <Input
            value={value === undefined || value === null ? "" : String(value)}
            onChange={(e) => onChange(e.target.value || undefined)}
            placeholder={prop.type === "string" ? "" : "value or {{template}}"}
          />
          <TemplateInsert
            contextKeys={contextKeys}
            onInsert={(t) => onChange(`${value ?? ""}${t}`)}
          />
        </div>
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
