import { describe, expect, it } from "vitest";
import { fieldGroups, type FieldContext } from "./fields";
import type { FieldCatalog, Vocabulary } from "./api";
import type { Trigger } from "./recipe";

const CATALOG: FieldCatalog = {
  trigger_fields: [
    { path: "trigger.event_type", label: "Event type" },
    { path: "trigger.source_system", label: "Source" },
  ],
  payload_by_event: {
    "lead.created": [
      { path: "trigger.payload.phone", label: "Phone" },
      { path: "trigger.payload.summary", label: "Summary" },
    ],
  },
  entities: {
    lead: {
      label: "Lead",
      fields: [{ path: "entity.status", label: "Status" }],
    },
  },
  event_entity: { "lead.created": "lead" },
};

// A Vocabulary with just the fields fieldGroups reads. Other required props are
// stubbed so the shape typechecks.
function vocab(over: Partial<Vocabulary>): Vocabulary {
  return {
    triggers: { event_types: [], source_systems: [] },
    tools: [],
    functions: [],
    operators: [],
    generate_models: [],
    field_roots: [],
    field_suggestions: [],
    field_catalog: CATALOG,
    ...over,
  };
}

const ctx = (trigger: Trigger, v: Vocabulary | null, contextKeys: string[] = []): FieldContext => ({
  vocabulary: v,
  trigger,
  contextKeys,
});

const titles = (groups: ReturnType<typeof fieldGroups>) => groups.map((g) => g.title);

describe("fieldGroups — event trigger with a catalog", () => {
  const groups = fieldGroups(
    ctx({ type: "event", event_type: "lead.created" }, vocab({})),
  );

  it("puts core trigger fields + the event's payload under 'From the trigger event'", () => {
    const g = groups.find((x) => x.title === "From the trigger event")!;
    const paths = g.items.map((it) => it.path);
    expect(paths).toContain("trigger.event_type"); // core
    expect(paths).toContain("trigger.source_system"); // core
    expect(paths).toContain("trigger.payload.phone"); // payload
    expect(paths).toContain("trigger.payload.summary"); // payload
    expect(g.hint).toBeUndefined();
  });

  it("includes the mapped record group with a labeled title and its fields", () => {
    const g = groups.find((x) => x.title === "The Lead")!;
    expect(g).toBeDefined();
    expect(g.items.map((it) => it.path)).toContain("entity.status");
  });

  it("appends earlier step results when contextKeys are present", () => {
    const withCtx = fieldGroups(
      ctx({ type: "event", event_type: "lead.created" }, vocab({}), ["message"]),
    );
    const g = withCtx.find((x) => x.title === "Earlier step results")!;
    expect(g.items[0].path).toBe("context.message");
  });
});

describe("fieldGroups — event trigger with no event_type", () => {
  const groups = fieldGroups(ctx({ type: "event", event_type: "" }, vocab({})));

  it("still offers the core trigger fields", () => {
    const g = groups.find((x) => x.title === "From the trigger event")!;
    expect(g.items.map((it) => it.path)).toContain("trigger.event_type");
    expect(g.hint).toMatch(/pick an event/i);
  });

  it("shows a pick-an-event hint for the record group instead of fields", () => {
    const g = groups.find((x) => x.title === "The record")!;
    expect(g.items).toHaveLength(0);
    expect(g.hint).toMatch(/pick an event/i);
  });
});

describe("fieldGroups — cron / manual triggers", () => {
  it("cron: a single hint-only group, no trigger/record fields", () => {
    const groups = fieldGroups(ctx({ type: "cron", expression: "0 9 * * 1" }, vocab({})));
    expect(titles(groups)).toEqual(["From the trigger"]);
    expect(groups[0].items).toHaveLength(0);
    expect(groups[0].hint).toBeTruthy();
  });

  it("manual: hint-only, but earlier step results still appear when present", () => {
    const groups = fieldGroups(ctx({ type: "manual" }, vocab({}), ["draft"]));
    expect(titles(groups)).toContain("From the trigger");
    expect(titles(groups)).toContain("Earlier step results");
  });
});

describe("fieldGroups — no catalog (older backend)", () => {
  const noCatalog = vocab({
    field_catalog: undefined,
    field_suggestions: ["trigger.event_type", "trigger.payload.phone", "entity.status"],
  });
  const groups = fieldGroups(ctx({ type: "event", event_type: "lead.created" }, noCatalog));

  it("falls back to humanized flat-suggestion groups", () => {
    const trig = groups.find((x) => x.title === "From the trigger event")!;
    expect(trig.items.map((it) => it.path)).toEqual([
      "trigger.event_type",
      "trigger.payload.phone",
    ]);
    const rec = groups.find((x) => x.title === "The record")!;
    expect(rec.items.map((it) => it.path)).toEqual(["entity.status"]);
    // Humanized labels (no catalog to name them).
    expect(trig.items[0].label).toBe("Event type");
  });
});
