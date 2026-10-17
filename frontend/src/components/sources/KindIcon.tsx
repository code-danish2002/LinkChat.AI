import { Icon, type IconName } from "@/components/ui/Icon";
import type { SourceKind } from "@/lib/api/types";

const BY_KIND: Record<SourceKind, { icon: IconName; tone: string }> = {
  youtube: { icon: "youtube", tone: "text-red-500" },
  web: { icon: "globe", tone: "text-sky-500" },
  pdf: { icon: "file", tone: "text-amber-500" },
  text: { icon: "file", tone: "text-slate-500" },
  docx: { icon: "file", tone: "text-blue-500" },
  pptx: { icon: "presentation", tone: "text-orange-500" },
  xlsx: { icon: "table", tone: "text-emerald-500" },
  epub: { icon: "book", tone: "text-violet-500" },
  audio: { icon: "mic", tone: "text-pink-500" },
};

export function KindIcon({ kind, className = "h-4 w-4" }: { kind: SourceKind; className?: string }) {
  const entry = BY_KIND[kind] ?? BY_KIND.web;
  return <Icon name={entry.icon} className={`${className} ${entry.tone}`} />;
}
