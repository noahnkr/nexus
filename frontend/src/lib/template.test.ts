import { describe, expect, it } from "vitest";
import {
  labelForPath,
  labelizeTemplate,
  parseTemplate,
  serializeTemplate,
  type Segment,
} from "./template";
import type { FieldCatalog } from "@/lib/api";

const rt = (s: string) => serializeTemplate(parseTemplate(s));

describe("parseTemplate / serializeTemplate", () => {
  it("round-trips plain text unchanged", () => {
    expect(rt("just some text")).toBe("just some text");
    expect(rt("")).toBe("");
  });

  it("parses a single full token", () => {
    const segs = parseTemplate("{{trigger.payload.phone}}");
    expect(segs).toEqual<Segment[]>([
      { kind: "token", value: "trigger.payload.phone" },
    ]);
    expect(serializeTemplate(segs)).toBe("{{trigger.payload.phone}}");
  });

  it("parses mixed text and tokens", () => {
    const segs = parseTemplate("Hi {{entity.name}}, welcome!");
    expect(segs).toEqual<Segment[]>([
      { kind: "text", value: "Hi " },
      { kind: "token", value: "entity.name" },
      { kind: "text", value: ", welcome!" },
    ]);
    expect(rt("Hi {{entity.name}}, welcome!")).toBe("Hi {{entity.name}}, welcome!");
  });

  it("parses adjacent tokens with no text between", () => {
    const segs = parseTemplate("{{a.b}}{{c.d}}");
    expect(segs.map((s) => s.kind)).toEqual(["token", "token"]);
    expect(rt("{{a.b}}{{c.d}}")).toBe("{{a.b}}{{c.d}}");
  });

  it("keeps malformed braces as text (round-trip preserved)", () => {
    expect(rt("{{unclosed")).toBe("{{unclosed");
    expect(rt("dangling }}")).toBe("dangling }}");
    expect(rt("{{}}")).toBe("{{}}"); // empty braces are not a token
    expect(parseTemplate("{{unclosed")).toEqual<Segment[]>([
      { kind: "text", value: "{{unclosed" },
    ]);
  });

  it("normalizes whitespace inside braces to the canonical form (backend tolerance)", () => {
    const segs = parseTemplate("{{  entity.name  }}");
    expect(segs).toEqual<Segment[]>([{ kind: "token", value: "entity.name" }]);
    expect(serializeTemplate(segs)).toBe("{{entity.name}}");
  });
});

const CATALOG: FieldCatalog = {
  trigger_fields: [{ path: "trigger.event_type", label: "Event type" }],
  payload_by_event: {
    "lead.created": [{ path: "trigger.payload.phone", label: "Phone" }],
  },
  entities: {
    lead: { label: "Lead", fields: [{ path: "entity.status", label: "Status" }] },
  },
  event_entity: { "lead.created": "lead" },
};

describe("labelForPath", () => {
  it("uses the catalog trigger-field label", () => {
    expect(labelForPath("trigger.event_type", CATALOG)).toBe("Event type");
  });

  it("uses a payload field label from any event", () => {
    expect(labelForPath("trigger.payload.phone", CATALOG)).toBe("Phone");
  });

  it("prefixes an entity field with the record label", () => {
    expect(labelForPath("entity.status", CATALOG)).toBe("Lead — Status");
  });

  it("labels a context path as a step result", () => {
    expect(labelForPath("context.message", CATALOG)).toBe("Step result: message");
  });

  it("falls back to a humanized tail for unknown paths", () => {
    expect(labelForPath("trigger.payload.hours_per_week")).toBe("Hours per week");
    expect(labelForPath("entity.years_experience")).toBe("Years experience");
  });
});

describe("labelizeTemplate", () => {
  it("replaces tokens with labels and keeps surrounding text", () => {
    expect(labelizeTemplate("Text {{trigger.payload.phone}}", CATALOG)).toBe("Text Phone");
    expect(labelizeTemplate("Hi {{entity.status}}", CATALOG)).toBe("Hi Lead — Status");
  });

  it("leaves token-free text untouched", () => {
    expect(labelizeTemplate("no tokens here")).toBe("no tokens here");
  });
});
