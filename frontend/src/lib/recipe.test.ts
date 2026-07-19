import { describe, expect, it } from "vitest";
import {
  eventTypeLabel,
  operatorLabel,
  OPERATORS,
  EVENT_TYPE_LABELS,
} from "./recipe";

describe("eventTypeLabel", () => {
  it("uses the curated label for core-known types", () => {
    expect(eventTypeLabel("lead.stage_changed")).toBe("Lead stage changed");
    expect(eventTypeLabel("sms.received")).toBe("Text message received");
  });

  it("humanizes an unknown type", () => {
    expect(eventTypeLabel("visit.checked_in")).toBe("Visit checked in");
    expect(eventTypeLabel("applicant.background_check_passed")).toBe(
      "Applicant background check passed",
    );
  });

  it("has a stable fallback for empty input", () => {
    expect(eventTypeLabel(undefined)).toBe("an event");
    expect(eventTypeLabel("")).toBe("an event");
  });

  it("every curated label is non-empty", () => {
    for (const label of Object.values(EVENT_TYPE_LABELS)) {
      expect(label.length).toBeGreaterThan(0);
    }
  });
});

describe("operatorLabel", () => {
  it("covers every vocabulary operator with a plain-language label", () => {
    for (const op of OPERATORS) {
      const label = operatorLabel(op);
      expect(label.length).toBeGreaterThan(0);
      // Snake_case tokens are always spelled out (never left with an underscore).
      expect(label).not.toContain("_");
    }
  });

  it("maps the common comparisons", () => {
    expect(operatorLabel("eq")).toBe("is");
    expect(operatorLabel("neq")).toBe("is not");
    expect(operatorLabel("gt")).toBe("is greater than");
    expect(operatorLabel("exists")).toBe("has a value");
    expect(operatorLabel("not_exists")).toBe("is empty");
  });

  it("falls back to a humanized token for an unknown operator", () => {
    expect(operatorLabel("starts_with")).toBe("starts with");
  });
});
