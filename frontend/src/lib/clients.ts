// Clients view — vertical content seam (Module 16), the frontend mirror of
// backend/app/services/views/clients.py. Status/payer labels and the hours
// formatting live here, not in core UI, so the care-oversight surface stays
// re-templatable (it sits alongside lib/leads.ts / lib/caregivers.ts). Unlike
// those, Clients is not a pipeline funnel — statuses are a small lifecycle
// (active / hospital_hold / discharged), so this is a plain module, no
// registerPipelineView. Pure + vitest-covered.
import type { ClientStatus, Payer } from "@/lib/api";

// Badge variants this config uses (subset of the ui/badge variants).
type BadgeTone = "success" | "warning" | "secondary";

export interface StatusMeta {
  label: string;
  badge: BadgeTone;
  dot: string; // dot color class for the ui/Select option
  terminal: boolean;
}

// active = success (currently served), hospital_hold = warning (hours suspended,
// they come back), discharged = muted/secondary (end of service, history stays).
export const CLIENT_STATUS_META: Record<ClientStatus, StatusMeta> = {
  active: { label: "Active", badge: "success", dot: "bg-success", terminal: false },
  hospital_hold: {
    label: "Hospital hold",
    badge: "warning",
    dot: "bg-warning",
    terminal: false,
  },
  discharged: {
    label: "Discharged",
    badge: "secondary",
    dot: "bg-muted-foreground",
    terminal: true,
  },
};

export const CLIENT_STATUSES: ClientStatus[] = [
  "active",
  "hospital_hold",
  "discharged",
];

export function statusMeta(status: string): StatusMeta {
  return (
    CLIENT_STATUS_META[status as ClientStatus] ?? {
      label: status,
      badge: "secondary",
      dot: "bg-muted-foreground",
      terminal: false,
    }
  );
}

export function statusLabel(status: string): string {
  return statusMeta(status).label;
}

// Who pays. A null payer is an intake in progress — labelled "Unknown", never
// dropped (mirrors the census which buckets null as 'unknown').
export const PAYER_LABELS: Record<Payer | "unknown", string> = {
  private_pay: "Private pay",
  medicaid: "Medicaid",
  ltc_insurance: "LTC insurance",
  va: "VA",
  other: "Other",
  unknown: "Unknown",
};

export const PAYERS: Payer[] = ["private_pay", "medicaid", "ltc_insurance", "va", "other"];

export function payerLabel(payer: string | null | undefined): string {
  if (!payer) return "Unknown";
  return PAYER_LABELS[payer as Payer] ?? payer;
}

// --- Hours formatting ---------------------------------------------------------
// A whole number shows without a trailing ".0" ("38 h"); a fraction keeps its
// tenth ("38.5 h"). The census reads in tenths of an hour.
export function fmtHours(n: number | null | undefined): string {
  const v = n ?? 0;
  const rounded = Math.round(v * 10) / 10;
  const text = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
  return `${text} h`;
}

// Clocked duration between two ISO stamps as "4h 10m" — the actual delivered time
// shown once a visit is checked in and out. Returns null if either stamp is
// missing or the range is non-positive (nothing meaningful to show).
export function fmtDuration(
  checkIn: string | null | undefined,
  checkOut: string | null | undefined,
): string | null {
  if (!checkIn || !checkOut) return null;
  const ms = new Date(checkOut).getTime() - new Date(checkIn).getTime();
  if (!Number.isFinite(ms) || ms <= 0) return null;
  const totalMinutes = Math.round(ms / 60000);
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}
