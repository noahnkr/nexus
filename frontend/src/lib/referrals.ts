// Referrals dashboard — vertical content seam (Module 17), the frontend mirror of
// backend/app/services/views/referrals.py. Category labels + dot tones, hours-won /
// rate formatting, the monthly-bucket fill, and the client-side table sort all live
// here (not in core UI) so the referral surface stays re-templatable, alongside
// lib/leads.ts / lib/clients.ts / lib/caregivers.ts. Pure + vitest-covered.
import type { MonthCount, PartnerCategory, ReferralSourceRow } from "@/lib/api";

export interface CategoryMeta {
  label: string;
  dot: string; // dot color class for the ui/Select option + the table chip
}

// One saturated hue per real category; other/untyped fall back to muted. Tones are
// semantic tokens (theme-aware in both light and dark) — see src/index.css.
export const PARTNER_CATEGORY_META: Record<PartnerCategory, CategoryMeta> = {
  hospital: { label: "Hospital", dot: "bg-info" },
  senior_living: { label: "Senior living", dot: "bg-success" },
  discharge_planner: { label: "Discharge planner", dot: "bg-warning" },
  home_health: { label: "Home health", dot: "bg-primary" },
  community: { label: "Community", dot: "bg-destructive" },
  other: { label: "Other", dot: "bg-muted-foreground" },
};

// Ordered for the category Select (matches the backend PARTNER_CATEGORIES order).
export const PARTNER_CATEGORIES: PartnerCategory[] = [
  "hospital",
  "senior_living",
  "discharge_planner",
  "home_health",
  "community",
  "other",
];

const UNTYPED_META: CategoryMeta = { label: "Untyped", dot: "bg-muted-foreground" };

export function categoryMeta(category: string | null | undefined): CategoryMeta {
  if (!category) return UNTYPED_META;
  return PARTNER_CATEGORY_META[category as PartnerCategory] ?? { label: category, dot: "bg-muted-foreground" };
}

export function categoryLabel(category: string | null | undefined): string {
  return categoryMeta(category).label;
}

// --- Formatting --------------------------------------------------------------
// Hours-won reads in tenths, like the census. Whole numbers drop the ".0".
function trimNumber(n: number): string {
  const rounded = Math.round(n * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}

export function fmtHoursWon(n: number | null | undefined): string {
  return `${trimNumber(n ?? 0)} hrs/wk`;
}

// Conversion rate as a percent, dropping a trailing ".0" ("100%", "33.3%").
export function fmtRate(n: number | null | undefined): string {
  return `${trimNumber(n ?? 0)}%`;
}

// --- Monthly trend -----------------------------------------------------------
// 'YYYY-MM' -> short month label ("2026-07" -> "Jul"). Parses the key directly (no
// timezone shift from Date on a day-less string).
export function monthLabel(key: string): string {
  const [y, m] = key.split("-").map(Number);
  if (!y || !m || m < 1 || m > 12) return key;
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString(undefined, {
    month: "short",
    timeZone: "UTC",
  });
}

// Ensure every month in `months` has a bucket (zero when absent), in window order.
// The backend already zero-fills, but this keeps the bars honest if a caller passes
// a sparse series (and it's the single place the sparkline reads its data).
export function fillMonths(monthly: MonthCount[], months: string[]): MonthCount[] {
  const byMonth = new Map(monthly.map((b) => [b.month, b.count]));
  return months.map((month) => ({ month, count: byMonth.get(month) ?? 0 }));
}

// Bar height as a 0–100% of the tallest bucket (min 4% so a nonzero bar is visible).
export function barPct(count: number, max: number): number {
  if (max <= 0 || count <= 0) return 0;
  return Math.max(4, Math.round((count / max) * 100));
}

// --- Sorting -----------------------------------------------------------------
export type SortKey =
  | "source"
  | "leads_total"
  | "converted"
  | "conversion_rate"
  | "hours_won"
  | "last_lead_at";

export type SortDir = "asc" | "desc";

// User decision: default to most won business first, then most leads.
export const DEFAULT_SORT: { key: SortKey; dir: SortDir } = {
  key: "hours_won",
  dir: "desc",
};

function keyValue(row: ReferralSourceRow, key: SortKey): number | string {
  switch (key) {
    case "source":
      return row.source.toLowerCase();
    case "last_lead_at":
      // Null (a tracked-but-quiet partner) sorts as the oldest possible time.
      return row.last_lead_at ? new Date(row.last_lead_at).getTime() : 0;
    default:
      return row[key];
  }
}

export function sortSources(
  rows: ReferralSourceRow[],
  key: SortKey,
  dir: SortDir,
): ReferralSourceRow[] {
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const av = keyValue(a, key);
    const bv = keyValue(b, key);
    let primary: number;
    if (typeof av === "string" && typeof bv === "string") {
      primary = av.localeCompare(bv);
    } else {
      primary = (av as number) - (bv as number);
    }
    if (primary !== 0) return primary * sign;
    // Stable tiebreak, direction-independent: more leads first, then name.
    if (b.leads_total !== a.leads_total) return b.leads_total - a.leads_total;
    return a.source.localeCompare(b.source);
  });
}
