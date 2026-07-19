import { Select, type SelectOption } from "@/components/ui/Select";
import { LEAD_STAGES } from "@/lib/leads";
import type { LeadStatus } from "@/lib/api";

// Stage-tone → dot color, so the picker shows the same status color as the funnel
// badges (tones live in lib/leads.ts, the leads content seam).
const TONE_DOT: Record<string, string> = {
  info: "bg-info",
  success: "bg-success",
  secondary: "bg-muted-foreground",
  default: "bg-primary",
};

const STAGE_OPTIONS: SelectOption<LeadStatus>[] = LEAD_STAGES.map((s) => ({
  value: s.key,
  label: s.label,
  dot: TONE_DOT[s.tone] ?? "bg-primary",
}));

// The lead's stage picker (profile). Changing it PATCHes leads.status, which emits
// lead.stage_changed — so the timeline gains the move and 9b's per-stage sequence
// fires. Shows plain-language labels + a stage-color dot; values are the raw keys.
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
    <Select
      value={status}
      onChange={onChange}
      options={STAGE_OPTIONS}
      disabled={disabled}
      aria-label="Lead stage"
    />
  );
}
