import { Badge } from "@/components/ui/badge";
import type { DocumentOut } from "@/lib/api";

const map: Record<DocumentOut["status"], { label: string; variant: any }> = {
  uploaded: { label: "Uploaded", variant: "secondary" },
  processing: { label: "Processing", variant: "default" },
  ready: { label: "Ready", variant: "success" },
  failed: { label: "Failed", variant: "destructive" },
};

export function StatusBadge({ status }: { status: DocumentOut["status"] }) {
  const { label, variant } = map[status] ?? { label: status, variant: "outline" };
  return <Badge variant={variant}>{label}</Badge>;
}
