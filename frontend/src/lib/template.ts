// Pure `{{path}}` template tokenizer (Module 11b) — no React. This is the only
// intricate logic behind the token chips; vitest covers it exhaustively so the
// TokenText component stays a thin segments↔DOM mapper. The stored recipe format is
// unchanged: chips serialize back to the exact `{{path}}` strings the engine renders.
import type { FieldCatalog } from "@/lib/api";

export type Segment =
  | { kind: "text"; value: string }
  | { kind: "token"; value: string }; // value = the trimmed dotted path

// Mirrors the backend `_TOKEN` regex (services/automations/templates.py):
// {{ dotted.path }} with whitespace tolerated inside braces, lazy so `{{a}}{{b}}`
// yields two tokens. `[^}]+?` requires at least one non-} char, so `{{}}` is text.
const TOKEN_SOURCE = "\\{\\{\\s*([^}]+?)\\s*\\}\\}";

export function parseTemplate(s: string): Segment[] {
  const segments: Segment[] = [];
  const re = new RegExp(TOKEN_SOURCE, "g");
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s)) !== null) {
    if (m.index > last) segments.push({ kind: "text", value: s.slice(last, m.index) });
    segments.push({ kind: "token", value: m[1].trim() });
    last = m.index + m[0].length;
  }
  if (last < s.length) segments.push({ kind: "text", value: s.slice(last) });
  return segments;
}

export function serializeTemplate(segs: Segment[]): string {
  return segs
    .map((seg) => (seg.kind === "token" ? `{{${seg.value}}}` : seg.value))
    .join("");
}

function humanizeTail(path: string): string {
  const tail = path.split(".").pop() ?? path;
  const spaced = tail.replace(/_/g, " ").trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : path;
}

// Plain-language label for a `{{path}}` reference. Resolution order (Module 11b):
// catalog trigger field → any event's payload field → entity field (prefixed with
// the record's label, e.g. "Lead — Status") → context step-result → humanized tail.
// An unknown path never throws — it renders its own humanized tail.
export function labelForPath(
  path: string,
  catalog?: FieldCatalog,
  contextKeys?: string[],
): string {
  if (catalog) {
    const trig = catalog.trigger_fields.find((f) => f.path === path);
    if (trig) return trig.label;
    for (const refs of Object.values(catalog.payload_by_event)) {
      const hit = refs.find((f) => f.path === path);
      if (hit) return hit.label;
    }
    for (const ent of Object.values(catalog.entities)) {
      const hit = ent.fields.find((f) => f.path === path);
      if (hit) return `${ent.label} — ${hit.label}`;
    }
  }
  if (path.startsWith("context.")) {
    const key = path.slice("context.".length);
    if (!contextKeys || contextKeys.includes(key)) return `Step result: ${key}`;
  }
  return humanizeTail(path);
}

// Replace every `{{path}}` in a string with its plain-language label — for read-mode
// surfaces (step cards, run timeline, recipe sentences). Text between tokens is kept
// verbatim; raw JSON in technical expanders is NOT run through this.
export function labelizeTemplate(s: string, catalog?: FieldCatalog): string {
  return parseTemplate(s)
    .map((seg) => (seg.kind === "token" ? labelForPath(seg.value, catalog) : seg.value))
    .join("");
}
