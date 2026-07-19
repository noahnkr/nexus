import { describe, expect, it } from "vitest";
import { checkFormula } from "./formula";

// Mirrors backend/tests/test_functions.py's grammar cases. This parser only
// validates — the backend computes — so the assertions are about ok/error, and
// about {{token}} handling, which the backend never sees (the engine substitutes
// tokens before its parser runs).

describe("checkFormula — valid expressions", () => {
  const valid = [
    "2+3*4",
    "(2+3)*4",
    "10/4",
    "-3 + 5",
    "--3",
    "2 * -4",
    "  7  ",
    "1.5 * 2",
    ".5 + 1",
    "round(10/3, 2)",
    "round(10/3)",
    "(1 + 2) * (3 + 4)",
  ];
  for (const expr of valid) {
    it(`accepts ${JSON.stringify(expr)}`, () => {
      expect(checkFormula(expr)).toEqual({ ok: true });
    });
  }
});

describe("checkFormula — field tokens stand in for numbers", () => {
  const valid = [
    "{{trigger.record.hourly_rate}}",
    "{{trigger.record.hourly_rate}} * 1.5",
    "({{entity.years_experience}} + 2) * 1.5",
    "round({{entity.visits}} / 4, 1)",
    "{{a.b}} + {{c.d}}",
    "-{{a.b}}",
  ];
  for (const expr of valid) {
    it(`accepts ${JSON.stringify(expr)}`, () => {
      expect(checkFormula(expr)).toEqual({ ok: true });
    });
  }

  it("still catches a missing operator between tokens", () => {
    const res = checkFormula("{{a.b}} {{c.d}}");
    expect(res.ok).toBe(false);
    expect(res.error).toContain("Unexpected");
  });

  it("treats a token as one unit regardless of the path's length", () => {
    expect(checkFormula("{{a.very.long.path.indeed}} * 2")).toEqual({ ok: true });
  });
});

describe("checkFormula — errors are plain language", () => {
  const cases: [string, string][] = [
    ["2 +", "ends unexpectedly"],
    ["(2 + 3", "Expected ')'"],
    ["2 + 3)", "Unexpected ')'"],
    ["2 3", "Unexpected '3'"],
    ["1/0", "Division by zero"],
    ["2 + pending", "is not a number"],
    ["nonsense(2)", "is not a number"],
    ["2 $ 3", "isn't something I can calculate"],
    ["", "empty"],
    ["   ", "empty"],
    ["round(2,)", "Unexpected ')'"],
    ["1.2.3 + 1", "isn't a valid number"],
  ];
  for (const [expr, fragment] of cases) {
    it(`rejects ${JSON.stringify(expr)}`, () => {
      const res = checkFormula(expr);
      expect(res.ok).toBe(false);
      expect(res.error).toContain(fragment);
    });
  }

  it("rejects an over-long formula", () => {
    const res = checkFormula("1+".repeat(300) + "1");
    expect(res.ok).toBe(false);
    expect(res.error).toContain("too long");
  });
});

describe("checkFormula — never evaluates anything", () => {
  // The backend parser is the security boundary, but this mirror must not become
  // a soft spot either: none of these may be reported as valid.
  const attacks = [
    "__import__('os')",
    "().__class__",
    "1 if true else 2",
    "alert(1)",
    "constructor",
  ];
  for (const expr of attacks) {
    it(`rejects ${JSON.stringify(expr)}`, () => {
      expect(checkFormula(expr).ok).toBe(false);
    });
  }
});
