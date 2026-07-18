import { LEAD_STAGES } from "@/lib/leads";
import type { LeadStatus } from "@/lib/api";

const selectClass =
  "h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

// The lead's stage picker (profile). Changing it PATCHes leads.status, which emits
// lead.stage_changed — so the timeline gains the move and 9b's per-stage sequence
// fires. Shows plain-language labels; values are the raw status keys.
export function StageSelect({
  status,
  onChange,
  disabled,
}: {
  status: LeadStatus;
  onChange: (status: LeadStatus) => void;
  disabled?: boolean;
}) {
  return (
    <select
      className={selectClass}
      value={status}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value as LeadStatus)}
    >
      {LEAD_STAGES.map((s) => (
        <option key={s.key} value={s.key}>
          {s.label}
        </option>
      ))}
    </select>
  );
}
