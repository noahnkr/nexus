import { describe, expect, it } from "vitest";
import {
  DEFAULT_SORT,
  PARTNER_CATEGORIES,
  PARTNER_CATEGORY_META,
  barPct,
  categoryLabel,
  categoryMeta,
  fillMonths,
  fmtHoursWon,
  fmtRate,
  monthLabel,
  sortSources,
} from "./referrals";
import type { PartnerCategory, ReferralSourceRow } from "@/lib/api";

describe("category meta", () => {
  it("covers every category the backend emits", () => {
    const expected: PartnerCategory[] = [
      "hospital",
      "senior_living",
      "discharge_planner",
      "home_health",
      "community",
      "other",
    ];
    expect(PARTNER_CATEGORIES).toEqual(expected);
    for (const c of expected) {
      expect(PARTNER_CATEGORY_META[c]).toBeDefined();
      expect(PARTNER_CATEGORY_META[c].label.length).toBeGreaterThan(0);
      expect(PARTNER_CATEGORY_META[c].dot.startsWith("bg-")).toBe(true);
    }
  });

  it("labels a null/unknown category as Untyped and never throws", () => {
    expect(categoryLabel(null)).toBe("Untyped");
    expect(categoryLabel(undefined)).toBe("Untyped");
    expect(categoryMeta(null).dot).toBe("bg-muted-foreground");
    expect(categoryLabel("hospital")).toBe("Hospital");
    expect(categoryLabel("weird")).toBe("weird");
  });
});

describe("formatting", () => {
  it("formats hours-won, dropping a trailing .0", () => {
    expect(fmtHoursWon(65)).toBe("65 hrs/wk");
    expect(fmtHoursWon(25.5)).toBe("25.5 hrs/wk");
    expect(fmtHoursWon(null)).toBe("0 hrs/wk");
    expect(fmtHoursWon(undefined)).toBe("0 hrs/wk");
  });

  it("formats a conversion rate as a percent", () => {
    expect(fmtRate(100)).toBe("100%");
    expect(fmtRate(33.3)).toBe("33.3%");
    expect(fmtRate(0)).toBe("0%");
  });
});

describe("monthly trend", () => {
  it("turns a YYYY-MM key into a short month label", () => {
    expect(monthLabel("2026-07")).toBe("Jul");
    expect(monthLabel("2026-01")).toBe("Jan");
    expect(monthLabel("bogus")).toBe("bogus");
  });

  it("zero-fills a sparse series into the full window, in order", () => {
    const months = ["2026-05", "2026-06", "2026-07"];
    const filled = fillMonths([{ month: "2026-06", count: 4 }], months);
    expect(filled).toEqual([
      { month: "2026-05", count: 0 },
      { month: "2026-06", count: 4 },
      { month: "2026-07", count: 0 },
    ]);
  });

  it("scales bar height against the tallest bucket", () => {
    expect(barPct(0, 10)).toBe(0);
    expect(barPct(10, 10)).toBe(100);
    expect(barPct(5, 10)).toBe(50);
    // A nonzero-but-tiny bucket still shows a visible sliver.
    expect(barPct(1, 1000)).toBe(4);
    // Guard against an all-zero window (no divide-by-zero).
    expect(barPct(0, 0)).toBe(0);
  });
});

describe("sortSources", () => {
  const row = (over: Partial<ReferralSourceRow>): ReferralSourceRow => ({
    source: "x",
    partner: null,
    leads_total: 0,
    in_pipeline: 0,
    converted: 0,
    lost: 0,
    conversion_rate: 0,
    avg_days_to_convert: null,
    hours_won: 0,
    last_lead_at: null,
    monthly: [],
    ...over,
  });

  const rows = [
    row({ source: "website", hours_won: 40, leads_total: 3 }),
    row({ source: "Sunrise", hours_won: 25, leads_total: 1 }),
    row({ source: "phone", hours_won: 0, leads_total: 1 }),
  ];

  it("defaults to hours-won descending", () => {
    expect(DEFAULT_SORT).toEqual({ key: "hours_won", dir: "desc" });
    const sorted = sortSources(rows, DEFAULT_SORT.key, DEFAULT_SORT.dir);
    expect(sorted.map((r) => r.source)).toEqual(["website", "Sunrise", "phone"]);
  });

  it("sorts a text column case-insensitively and reverses on dir", () => {
    const asc = sortSources(rows, "source", "asc").map((r) => r.source);
    expect(asc).toEqual(["phone", "Sunrise", "website"]);
    const desc = sortSources(rows, "source", "desc").map((r) => r.source);
    expect(desc).toEqual(["website", "Sunrise", "phone"]);
  });

  it("breaks ties by leads then name, and does not mutate the input", () => {
    const tied = [
      row({ source: "b", hours_won: 10, leads_total: 2 }),
      row({ source: "a", hours_won: 10, leads_total: 5 }),
    ];
    const snapshot = tied.map((r) => r.source);
    const sorted = sortSources(tied, "hours_won", "desc");
    // Equal hours_won -> more leads first (a has 5).
    expect(sorted.map((r) => r.source)).toEqual(["a", "b"]);
    expect(tied.map((r) => r.source)).toEqual(snapshot); // input untouched
  });
});
