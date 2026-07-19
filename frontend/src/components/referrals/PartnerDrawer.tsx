import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Mail, Pencil, Phone, Plus, Trash2, User, X } from "lucide-react";
import { api, type Lead, type ReferralSourceRow } from "@/lib/api";
import { categoryMeta, fmtHoursWon, fmtRate } from "@/lib/referrals";
import { stageLabel, stageTone } from "@/lib/leads";
import { cn, relativeTime } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/automations/ConfirmDialog";
import { MonthlyTrendBars } from "./MonthlyTrendBars";

// lib/leads tones -> ui/badge variants (tone "default" isn't a badge variant).
const stageBadge = (status: string) => {
  const tone = stageTone(status);
  return tone === "default" ? "secondary" : tone;
};

// Right-side sheet for one source row. Tracked partners show their contact card with
// Edit + Delete; an untracked source shows a Track CTA. Both show the per-partner
// trend + that source's recent leads. Deleting only stops tracking — the leads keep
// their source and reappear untracked (no funnel history is touched).
export function PartnerDrawer({
  row,
  months,
  onClose,
  onEdit,
  onTrack,
  onDelete,
}: {
  row: ReferralSourceRow;
  months: string[];
  onClose: () => void;
  onEdit: (row: ReferralSourceRow) => void;
  onTrack: (source: string) => void;
  onDelete: (partnerId: string) => Promise<void>;
}) {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const partner = row.partner;
  const meta = partner ? categoryMeta(partner.category) : null;

  useEffect(() => {
    let cancelled = false;
    api
      .listLeads({ source: row.source, limit: 5 })
      .then((page) => !cancelled && setLeads(page.leads))
      .catch(() => !cancelled && setLeads([]));
    return () => {
      cancelled = true;
    };
  }, [row.source]);

  const doDelete = async () => {
    if (!partner) return;
    setBusy(true);
    try {
      await onDelete(partner.id);
      setConfirmDelete(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="absolute right-0 top-0 flex h-full w-full max-w-md flex-col border-l bg-card shadow-xl">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b p-4">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold">{row.source}</h2>
            <div className="mt-1">
              {meta ? (
                <Badge variant="outline" className="gap-1.5 font-normal">
                  <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} />
                  {meta.label}
                </Badge>
              ) : (
                <span className="text-xs text-muted-foreground">Untracked source</span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {partner ? (
              <Button size="sm" variant="outline" onClick={() => onEdit(row)}>
                <Pencil className="h-3.5 w-3.5" /> Edit
              </Button>
            ) : (
              <Button size="sm" variant="outline" onClick={() => onTrack(row.source)}>
                <Plus className="h-3.5 w-3.5" /> Track
              </Button>
            )}
            <button
              onClick={onClose}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
          {/* Stat row */}
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Leads" value={String(row.leads_total)} />
            <Stat
              label="Converted"
              value={row.leads_total > 0 ? `${row.converted} · ${fmtRate(row.conversion_rate)}` : "—"}
            />
            <Stat label="Hours/wk won" value={row.hours_won > 0 ? fmtHoursWon(row.hours_won) : "—"} />
          </div>
          <div className="grid grid-cols-3 gap-2">
            <Stat label="In pipeline" value={String(row.in_pipeline)} />
            <Stat label="Lost" value={String(row.lost)} />
            <Stat
              label="Avg days to win"
              value={row.avg_days_to_convert != null ? String(row.avg_days_to_convert) : "—"}
            />
          </div>

          {/* Contact card (tracked only) */}
          {partner && (
            <Section title="Contact">
              <div className="space-y-1.5 text-sm">
                <ContactLine icon={User} value={partner.contact_name} />
                <ContactLine icon={Phone} value={partner.phone} mono />
                <ContactLine icon={Mail} value={partner.email} mono />
                {partner.notes && (
                  <p className="pt-1 text-sm text-muted-foreground">{partner.notes}</p>
                )}
                {!partner.contact_name && !partner.phone && !partner.email && !partner.notes && (
                  <p className="text-sm text-muted-foreground">
                    No contact details yet — add them with Edit.
                  </p>
                )}
              </div>
            </Section>
          )}

          {/* Trend */}
          <Section title="Leads by month">
            <MonthlyTrendBars monthly={row.monthly} months={months} />
          </Section>

          {/* Recent leads */}
          <Section title="Recent leads">
            {leads.length === 0 ? (
              <p className="text-sm text-muted-foreground">No leads from this source yet.</p>
            ) : (
              <div className="flex flex-col gap-1.5">
                {leads.map((lead) => (
                  <Link
                    key={lead.id}
                    to={`/leads/${lead.id}`}
                    className="flex items-center justify-between rounded-md border px-3 py-2 text-sm transition-colors hover:border-primary/40"
                  >
                    <span className="min-w-0 truncate font-medium">{lead.name}</span>
                    <span className="flex shrink-0 items-center gap-2">
                      <Badge variant={stageBadge(lead.status)}>{stageLabel(lead.status)}</Badge>
                      <span className="text-xs text-muted-foreground">
                        {relativeTime(lead.created_at)}
                      </span>
                    </span>
                  </Link>
                ))}
              </div>
            )}
          </Section>

          {/* Delete (tracked only) */}
          {partner && (
            <div className="border-t pt-4">
              <Button
                size="sm"
                variant="ghost"
                className="text-destructive hover:text-destructive"
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2 className="h-4 w-4" /> Stop tracking this partner
              </Button>
            </div>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Stop tracking this partner?"
        body={`This removes '${row.source}' from your tracked partners. Its leads keep their source and funnel history — the source simply shows as untracked again.`}
        confirmLabel="Stop tracking"
        destructive
        onConfirm={doDelete}
        onClose={() => !busy && setConfirmDelete(false)}
      />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-muted/20 p-2.5">
      <div className="text-sm font-semibold tabular-nums">{value}</div>
      <div className="mt-0.5 text-[11px] text-muted-foreground">{label}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </div>
      {children}
    </div>
  );
}

function ContactLine({
  icon: Icon,
  value,
  mono,
}: {
  icon: React.ComponentType<{ className?: string }>;
  value: string | null;
  mono?: boolean;
}) {
  if (!value) return null;
  return (
    <div className="flex items-center gap-2">
      <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <span className={mono ? "font-mono text-xs" : undefined}>{value}</span>
    </div>
  );
}
