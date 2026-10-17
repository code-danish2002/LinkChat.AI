"use client";

import { SourceCard } from "@/components/sources/SourceCard";
import { Icon } from "@/components/ui/Icon";
import type { Source } from "@/lib/api/types";

export function SourceList({
  sources,
  loading,
  error,
  onDelete,
}: {
  sources: Source[];
  loading: boolean;
  error: string | null;
  onDelete: (id: string) => void;
}) {
  if (loading) {
    return (
      <div className="grid gap-3 sm:grid-cols-2">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-[76px] animate-pulse rounded-xl bg-slate-200/70 dark:bg-slate-800/70" />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex items-start gap-2 rounded-xl bg-rose-50 p-4 text-sm text-rose-700 ring-1 ring-rose-200 dark:bg-rose-950/30 dark:text-rose-300 dark:ring-rose-900">
        <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
        <span>{error}</span>
      </div>
    );
  }
  if (sources.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
        Nothing here yet. Paste a link above to get started.
      </div>
    );
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {sources.map((source) => (
        <SourceCard key={source.id} source={source} onDelete={onDelete} />
      ))}
    </div>
  );
}
