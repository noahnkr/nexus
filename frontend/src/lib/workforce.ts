// Workforce & compliance — vertical content seam (Module 18), the frontend mirror
// of backend/app/services/views/workforce.py. Credential/status labels and tones,
// utilization and days-left formatting, and the roster sort all live here (not in
// core UI) so the workforce surface stays re-templatable, alongside lib/leads.ts /
// lib/clients.ts / lib/caregivers.ts / lib/referrals.ts. Pure + vitest-covered.
//
// This module FORMATS server-computed values. It never derives a credential
// status or a utilization percentage — those come from the seam, so the badge on
// screen and the number in a digest task can never disagree.
import type {
  Credential,
  CredentialStatus,
  ResourceStatus,
  RosterCaregiver,
} from "@/lib/api";

// Badge variants this config uses (subset of the ui/badge variants).
type BadgeTone = "success" | "warning" | "destructive" | "secondary";

export interface CredentialStatusMeta {
  label: string;
  badge: BadgeTone;
  dot: string; // dot color class for chips and ui/Select options
  rank: number; // sort weight — worst first, so problems surface at a glance
}

// expired = destructive (they may legally not be able to work a shift),
// expiring = warning (act now), valid = success, no_expiry = muted (a one-time
// sign-off is fine, but it is NOT the same as "valid until a date").
export const CREDENTIAL_STATUS_META: Record<CredentialStatus, CredentialStatusMeta> = {
  expired: { label: "Expired", badge: "destructive", dot: "bg-destructive", rank: 0 },
  expiring: { label: "Expiring", badge: "warning", dot: "bg-warning", rank: 1 },
  valid: { label: "Valid", badge: "success", dot: "bg-success", rank: 2 },
  no_expiry: { label: "No expiry", badge: "secondary", dot: "bg-muted-foreground", rank: 3 },
};

export const CREDENTIAL_STATUSES: CredentialStatus[] = [
  "expired",
  "expiring",
  "valid",
  "no_expiry",
];

const UNKNOWN_CREDENTIAL_META: CredentialStatusMeta = {
  label: "Unknown",
  badge: "secondary",
  dot: "bg-muted-foreground",
  rank: 4,
};

export function credentialMeta(status: string | null | undefined): CredentialStatusMeta {
  if (!status) return UNKNOWN_CREDENTIAL_META;
  return (
    CREDENTIAL_STATUS_META[status as CredentialStatus] ?? {
      ...UNKNOWN_CREDENTIAL_META,
      label: status,
    }
  );
}

export interface ResourceStatusMeta {
  label: string;
  badge: BadgeTone;
  dot: string;
}

// Inactive is muted, never destructive — leaving is a normal lifecycle event, not
// an error state, and the person's history is kept either way.
export const RESOURCE_STATUS_META: Record<ResourceStatus, ResourceStatusMeta> = {
  active: { label: "Active", badge: "success", dot: "bg-success" },
  inactive: { label: "Inactive", badge: "secondary", dot: "bg-muted-foreground" },
};

export const RESOURCE_STATUSES: ResourceStatus[] = ["active", "inactive"];

export function resourceStatusMeta(status: string | null | undefined): ResourceStatusMeta {
  if (!status) return RESOURCE_STATUS_META.active;
  return (
    RESOURCE_STATUS_META[status as ResourceStatus] ?? {
      label: status,
      badge: "secondary",
      dot: "bg-muted-foreground",
    }
  );
}

// --- Formatting --------------------------------------------------------------
function trimNumber(n: number): string {
  const rounded = Math.round(n * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}

// Utilization is null when capacity is unknown (no declared availability) — that
// is NOT 0%, so it renders as an em dash. Display caps at 999% so one absurd
// number can't blow out the column; the underlying value is never clamped.
const UTILIZATION_DISPLAY_CAP = 999;

export function fmtUtilization(pct: number | null | undefined): string {
  if (pct == null) return "—";
  if (pct > UTILIZATION_DISPLAY_CAP) return `${UTILIZATION_DISPLAY_CAP}%+`;
  return `${trimNumber(pct)}%`;
}

// Bar width for the utilization meter, 0–100. Over-booked caregivers peg the bar
// full; the number beside it carries the real magnitude.
export function utilizationBarPct(pct: number | null | undefined): number {
  if (pct == null || pct <= 0) return 0;
  return Math.min(100, pct);
}

// Over their declared availability — the bar and the number both go warning-toned.
export function isOverbooked(pct: number | null | undefined): boolean {
  return pct != null && pct > 100;
}

export function fmtHours(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${trimNumber(n)}h`;
}

// "expires in 30 d" / "expired 10 d ago" / "expires today" / "no expiry".
// Mirrors the backend's describe_expiry phrasing, abbreviated for a table cell.
export function fmtDaysLeft(days: number | null | undefined): string {
  if (days == null) return "no expiry";
  if (days < 0) {
    const n = Math.abs(days);
    return `expired ${n} d ago`;
  }
  if (days === 0) return "expires today";
  return `expires in ${days} d`;
}

// "Mar 4, 2027" — a date the office user reads, from the API's YYYY-MM-DD string.
// Parsed as a LOCAL date (not `new Date("2027-03-04")`, which is UTC midnight and
// renders as the previous day west of Greenwich).
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// --- Sorting -----------------------------------------------------------------
// Credentials worst-first, then soonest expiry, then name — so a caregiver's
// problem credential is always the first chip in their row.
export function sortCredentials(credentials: Credential[]): Credential[] {
  return [...credentials].sort((a, b) => {
    const rank = credentialMeta(a.status).rank - credentialMeta(b.status).rank;
    if (rank !== 0) return rank;
    const ad = a.days_left ?? Number.MAX_SAFE_INTEGER;
    const bd = b.days_left ?? Number.MAX_SAFE_INTEGER;
    if (ad !== bd) return ad - bd;
    return a.qualification_name.localeCompare(b.qualification_name);
  });
}

// Roster order: active caregivers first (they're who you staff), then by name.
export function sortRoster(rows: RosterCaregiver[]): RosterCaregiver[] {
  return [...rows].sort((a, b) => {
    if (a.status !== b.status) return a.status === "active" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}

// Client-side search + status filter (roster scale is low tens — no server round
// trip). Matches name, phone, email, and credential names, case-insensitively.
export function filterRoster(
  rows: RosterCaregiver[],
  { search, status }: { search?: string; status?: string },
): RosterCaregiver[] {
  const q = (search ?? "").trim().toLowerCase();
  return rows.filter((r) => {
    if (status && r.status !== status) return false;
    if (!q) return true;
    const haystack = [
      r.name,
      r.phone ?? "",
      r.email ?? "",
      ...r.credentials.map((c) => c.qualification_name),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(q);
  });
}
