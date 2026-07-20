import { describe, expect, it } from "vitest";
import {
  CREDENTIAL_STATUSES,
  CREDENTIAL_STATUS_META,
  RESOURCE_STATUSES,
  RESOURCE_STATUS_META,
  credentialMeta,
  filterRoster,
  fmtDate,
  fmtDaysLeft,
  fmtHours,
  fmtUtilization,
  isOverbooked,
  resourceStatusMeta,
  sortCredentials,
  sortRoster,
  utilizationBarPct,
} from "./workforce";
import type { Credential, CredentialStatus, ResourceStatus, RosterCaregiver } from "@/lib/api";

function cred(over: Partial<Credential>): Credential {
  return {
    id: over.id ?? "c1",
    resource_id: "r1",
    qualification_id: "q1",
    qualification_name: over.qualification_name ?? "CPR",
    issued_at: null,
    expires_at: over.expires_at ?? null,
    status: over.status ?? "valid",
    days_left: over.days_left ?? null,
    notes: null,
  };
}

function caregiver(over: Partial<RosterCaregiver>): RosterCaregiver {
  return {
    id: over.id ?? "r1",
    name: over.name ?? "Alicia Moreno",
    phone: over.phone ?? null,
    email: over.email ?? null,
    status: over.status ?? "active",
    address: null,
    zip: null,
    languages: [],
    traits: [],
    qualification_ids: [],
    region_ids: [],
    availability: {},
    hours_this_week: over.hours_this_week ?? 0,
    available_hours: over.available_hours ?? null,
    utilization: over.utilization ?? null,
    credentials: over.credentials ?? [],
  };
}

describe("status meta", () => {
  it("covers every credential status the backend emits", () => {
    const expected: CredentialStatus[] = ["expired", "expiring", "valid", "no_expiry"];
    expect(CREDENTIAL_STATUSES).toEqual(expected);
    for (const s of expected) {
      const meta = CREDENTIAL_STATUS_META[s];
      expect(meta).toBeDefined();
      expect(meta.label.length).toBeGreaterThan(0);
      expect(meta.dot.startsWith("bg-")).toBe(true);
    }
    // Worst-first ranking is what the chip order depends on.
    expect(CREDENTIAL_STATUS_META.expired.rank).toBeLessThan(
      CREDENTIAL_STATUS_META.expiring.rank,
    );
    expect(CREDENTIAL_STATUS_META.expiring.rank).toBeLessThan(
      CREDENTIAL_STATUS_META.valid.rank,
    );
  });

  it("covers every resource status and never throws on an unknown one", () => {
    const expected: ResourceStatus[] = ["active", "inactive"];
    expect(RESOURCE_STATUSES).toEqual(expected);
    for (const s of expected) expect(RESOURCE_STATUS_META[s].label.length).toBeGreaterThan(0);
    expect(resourceStatusMeta("retired").label).toBe("retired");
    expect(resourceStatusMeta(null).label).toBe("Active");
    expect(credentialMeta("weird").label).toBe("weird");
    expect(credentialMeta(null).dot).toBe("bg-muted-foreground");
  });
});

describe("fmtUtilization", () => {
  it("renders unknown capacity as an em dash, not 0%", () => {
    expect(fmtUtilization(null)).toBe("—");
    expect(fmtUtilization(undefined)).toBe("—");
    // A real, measured zero is still a number.
    expect(fmtUtilization(0)).toBe("0%");
  });

  it("trims a trailing .0 and keeps one decimal otherwise", () => {
    expect(fmtUtilization(75)).toBe("75%");
    expect(fmtUtilization(66.7)).toBe("66.7%");
    expect(fmtUtilization(120)).toBe("120%");
  });

  it("caps the DISPLAY at 999% without clamping the value", () => {
    expect(fmtUtilization(1500)).toBe("999%+");
    expect(isOverbooked(1500)).toBe(true);
  });
});

describe("utilization bar", () => {
  it("pegs at 100 for overbooked and floors at 0 for unknown", () => {
    expect(utilizationBarPct(null)).toBe(0);
    expect(utilizationBarPct(0)).toBe(0);
    expect(utilizationBarPct(50)).toBe(50);
    expect(utilizationBarPct(180)).toBe(100);
  });

  it("flags overbooked only above 100", () => {
    expect(isOverbooked(null)).toBe(false);
    expect(isOverbooked(100)).toBe(false);
    expect(isOverbooked(100.5)).toBe(true);
  });
});

describe("fmtDaysLeft", () => {
  it("phrases past, present, and future", () => {
    expect(fmtDaysLeft(null)).toBe("no expiry");
    expect(fmtDaysLeft(-10)).toBe("expired 10 d ago");
    expect(fmtDaysLeft(-1)).toBe("expired 1 d ago");
    expect(fmtDaysLeft(0)).toBe("expires today");
    expect(fmtDaysLeft(30)).toBe("expires in 30 d");
  });
});

describe("fmtHours / fmtDate", () => {
  it("renders hours, dashing out unknown availability", () => {
    expect(fmtHours(null)).toBe("—");
    expect(fmtHours(24)).toBe("24h");
    expect(fmtHours(7.5)).toBe("7.5h");
  });

  it("parses the API date as LOCAL, so the day never slips backwards", () => {
    // new Date("2027-03-04") is UTC midnight and renders as Mar 3 west of GMT.
    expect(fmtDate("2027-03-04")).toContain("4");
    expect(fmtDate("2027-03-04")).toContain("2027");
    expect(fmtDate(null)).toBe("—");
    expect(fmtDate("garbage")).toBe("garbage");
  });
});

describe("sortCredentials", () => {
  it("puts problems first, then soonest expiry, then name", () => {
    const rows = [
      cred({ id: "valid", status: "valid", days_left: 180, qualification_name: "CNA" }),
      cred({ id: "none", status: "no_expiry", qualification_name: "Hoyer" }),
      cred({ id: "soon", status: "expiring", days_left: 30, qualification_name: "TB" }),
      cred({ id: "expired", status: "expired", days_left: -10, qualification_name: "HHA" }),
      cred({ id: "sooner", status: "expiring", days_left: 5, qualification_name: "CPR" }),
    ];
    expect(sortCredentials(rows).map((c) => c.id)).toEqual([
      "expired",
      "sooner",
      "soon",
      "valid",
      "none",
    ]);
  });

  it("does not mutate its input", () => {
    const rows = [cred({ id: "a", status: "valid" }), cred({ id: "b", status: "expired" })];
    sortCredentials(rows);
    expect(rows.map((c) => c.id)).toEqual(["a", "b"]);
  });
});

describe("sortRoster / filterRoster", () => {
  const rows = [
    caregiver({ id: "z", name: "Zoe Adams", status: "inactive" }),
    caregiver({ id: "b", name: "Brian Okafor", phone: "+16195550202" }),
    caregiver({
      id: "a",
      name: "Alicia Moreno",
      credentials: [cred({ qualification_name: "Hoyer Lift Certified" })],
    }),
  ];

  it("lists active caregivers first, then alphabetically", () => {
    expect(sortRoster(rows).map((r) => r.id)).toEqual(["a", "b", "z"]);
  });

  it("filters by status and searches name, phone, and credential names", () => {
    expect(filterRoster(rows, { status: "inactive" }).map((r) => r.id)).toEqual(["z"]);
    expect(filterRoster(rows, { search: "brian" }).map((r) => r.id)).toEqual(["b"]);
    expect(filterRoster(rows, { search: "5550202" }).map((r) => r.id)).toEqual(["b"]);
    expect(filterRoster(rows, { search: "hoyer" }).map((r) => r.id)).toEqual(["a"]);
    // An empty filter is a pass-through, and the two combine.
    expect(filterRoster(rows, {})).toHaveLength(3);
    expect(filterRoster(rows, { search: "o", status: "inactive" }).map((r) => r.id)).toEqual([
      "z",
    ]);
  });
});
