import { Icon } from "@/components/ui/Icon";
import type { SourceStatus } from "@/lib/api/types";

const styles: Record<SourceStatus, { className: string; label: string; icon: "spinner" | "check" | "alert" | "clock" }> = {
  pending: { className: "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-900", label: "Queued", icon: "clock" },
  processing: { className: "bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-950/40 dark:text-sky-300 dark:ring-sky-900", label: "Reading", icon: "spinner" },
  ready: { className: "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:ring-emerald-900", label: "Ready", icon: "check" },
  failed: { className: "bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-950/40 dark:text-rose-300 dark:ring-rose-900", label: "Failed", icon: "alert" },
};

export function StatusBadge({ status }: { status: SourceStatus }) {
  const style = styles[status];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${style.className}`}>
      <Icon name={style.icon} className="h-3 w-3" />
      {style.label}
    </span>
  );
}
