import { describe, expect, it } from "vitest";
import {
  CLIENT_STATUS_META,
  CLIENT_STATUSES,
  PAYERS,
  PAYER_LABELS,
  fmtDuration,
  fmtHours,
  payerLabel,
  statusLabel,
  statusMeta,
} from "./clients";
import type { ClientStatus, Payer } from "@/lib/api";

describe("status meta", () => {
  it("covers every status the backend emits", () => {
    const expected: ClientStatus[] = ["active", "hospital_hold", "discharged"];
    expect(CLIENT_STATUSES).toEqual(expected);
    for (const s of expected) {
      expect(CLIENT_STATUS_META[s]).toBeDefined();
      expect(CLIENT_STATUS_META[s].label.length).toBeGreaterThan(0);
    }
  });

  it("maps tones per the locked design (active=success, hold=warning, discharged=muted)", () => {
    expect(CLIENT_STATUS_META.active.badge).toBe("success");
    expect(CLIENT_STATUS_META.hospital_hold.badge).toBe("warning");
    expect(CLIENT_STATUS_META.discharged.badge).toBe("secondary");
    expect(CLIENT_STATUS_META.discharged.terminal).toBe(true);
  });

  it("falls back gracefully for an unknown status", () => {
    expect(statusMeta("weird").label).toBe("weird");
    expect(statusLabel("hospital_hold")).toBe("Hospital hold");
  });
});

describe("payer labels", () => {
  it("labels every payer key plus unknown", () => {
    const expected: Payer[] = ["private_pay", "medicaid", "ltc_insurance", "va", "other"];
    expect(PAYERS).toEqual(expected);
    for (const p of expected) expect(PAYER_LABELS[p].length).toBeGreaterThan(0);
    expect(PAYER_LABELS.unknown).toBe("Unknown");
  });

  it("labels null/undefined as Unknown", () => {
    expect(payerLabel(null)).toBe("Unknown");
    expect(payerLabel(undefined)).toBe("Unknown");
    expect(payerLabel("private_pay")).toBe("Private pay");
    expect(payerLabel("ltc_insurance")).toBe("LTC insurance");
  });
});

describe("fmtHours", () => {
  it("drops a trailing .0 on whole numbers", () => {
    expect(fmtHours(38)).toBe("38 h");
    expect(fmtHours(0)).toBe("0 h");
  });

  it("keeps a single decimal on fractions", () => {
    expect(fmtHours(38.5)).toBe("38.5 h");
    expect(fmtHours(6.25)).toBe("6.3 h");
  });

  it("treats null/undefined as zero", () => {
    expect(fmtHours(null)).toBe("0 h");
    expect(fmtHours(undefined)).toBe("0 h");
  });
});

describe("fmtDuration", () => {
  it("formats hours and minutes", () => {
    expect(fmtDuration("2026-07-19T08:04:00Z", "2026-07-19T12:14:00Z")).toBe("4h 10m");
  });

  it("collapses to a single unit when the other is zero", () => {
    expect(fmtDuration("2026-07-19T08:00:00Z", "2026-07-19T12:00:00Z")).toBe("4h");
    expect(fmtDuration("2026-07-19T08:00:00Z", "2026-07-19T08:30:00Z")).toBe("30m");
  });

  it("returns null when a stamp is missing or the range is non-positive", () => {
    expect(fmtDuration(null, "2026-07-19T12:00:00Z")).toBeNull();
    expect(fmtDuration("2026-07-19T08:00:00Z", null)).toBeNull();
    expect(fmtDuration("2026-07-19T12:00:00Z", "2026-07-19T08:00:00Z")).toBeNull();
  });
});
